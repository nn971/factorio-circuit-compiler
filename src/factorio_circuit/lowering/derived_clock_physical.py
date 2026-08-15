"""Physical realization of derived Event clocks.

Derived clocks extend the stateful Event lowerer rather than duplicating its payload/state cells.
A semantic clock becomes a shared physical valid-token net at the earliest phase where its defining
logic is available; callers requesting a later phase reuse the ordinary delay cache.
"""

from __future__ import annotations

from factorio_circuit.analysis.latency import FACTORIO_LATENCY
from factorio_circuit.analysis.state_timing import StateTimingPlan
from factorio_circuit.ir.abstract_physical import (
    AbstractPhysicalCircuit,
    ArithmeticCombinator,
    Connector,
    DeciderCombinator,
    Endpoint,
    Operand,
)
from factorio_circuit.ir.clocks import EventMerge, GateClock, SumInto
from factorio_circuit.ir.semantic import (
    CircuitModule,
    Clock,
    ClockId,
    EventInput,
    EventScalarFlow,
    EventVectorFlow,
    Flow,
    PayloadShape,
    ScalarValue,
    TemporalModality,
    VectorValue,
)
from factorio_circuit.ir.state import StateTransition
from factorio_circuit.lowering.clocked_physical import RealizedClock
from factorio_circuit.lowering.clocked_state_physical import StatefulClockedPhysicalLowerer
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedValue, RealizedVector
from factorio_circuit.lowering.vector_binary import vector_metadata
from factorio_circuit.target.factorio.signals import SIGNAL_EVERYTHING


class DerivedClockPhysicalLowerer(StatefulClockedPhysicalLowerer):
    """Stateful Event lowerer with compiler-owned derived clock realization."""

    def __init__(self, module: CircuitModule, *, state_timing: StateTimingPlan) -> None:
        super().__init__(module, state_timing=state_timing)
        self._derived_clock_sources: dict[ClockId, GateClock | EventMerge] = {
            source.clock.clock_id: source
            for source in module.event_inputs
            if isinstance(source, (GateClock, EventMerge))
        }
        self._merge_scalar_payloads: dict[EventMerge, RealizedValue] = {}
        self._merge_vector_payloads: dict[EventMerge, RealizedVector] = {}

    @staticmethod
    def _scalar_event(source: EventInput) -> EventScalarFlow:
        return EventScalarFlow(
            source,
            Flow(
                reference=source,
                payload_shape=PayloadShape.SCALAR,
                modality=TemporalModality.EVENT,
                clock=source.clock,
            ),
        )

    @staticmethod
    def _vector_event(source: EventInput) -> EventVectorFlow:
        return EventVectorFlow(
            source,
            Flow(
                reference=source,
                payload_shape=PayloadShape.VECTOR,
                modality=TemporalModality.EVENT,
                clock=source.clock,
            ),
        )

    def _realize_gate_clock(self, source: GateClock) -> RealizedClock:
        cached = self._clock_sources.get(source.clock.clock_id)
        if cached is not None:
            return cached

        predicate = self.realize(source.predicate)
        parent = self._clock_at(source.parent.clock, predicate.phase)
        predicate = self.delay_to(predicate, parent.phase)
        if predicate.net != parent.net:
            self._add_net_conflict(
                predicate.net,
                parent.net,
                f"GateClock {source.name}: predicate/parent-valid isolation",
            )

        gate = DeciderCombinator(
            id=self._take_entity_id(),
            comparator="!=",
            left=Operand(signal=predicate.signal, nets=(predicate.net,)),
            right=Operand(constant=0),
            output_signal=parent.signal,
            output_copy_count_from_input=True,
            copy_count_nets=(parent.net,),
            description=f"GateClock {source.name}: keep parent valid when predicate is nonzero",
        )
        self.circuit.entities.append(gate)
        gate_input = Endpoint(gate.id, Connector.INPUT)
        self._attach(predicate.net, gate_input)
        self._attach(parent.net, gate_input)
        output_net = self._new_net(
            (parent.signal,),
            Endpoint(gate.id, Connector.OUTPUT),
            label=f"GateClock {source.name}: derived valid",
        )

        phase = parent.phase + FACTORIO_LATENCY.operation_latency("compare", "gate_clock")
        result = RealizedClock(source.clock.clock_id, parent.signal, output_net, phase)
        self._clock_sources[source.clock.clock_id] = result
        self._clock_phase_cache[(source.clock.clock_id, phase)] = result
        return result

    def _realize_merge_valid(
        self,
        source: EventMerge,
        parents: list[RealizedClock],
        phase: int,
    ) -> RealizedClock:
        cached = self._clock_sources.get(source.clock.clock_id)
        if cached is not None:
            return cached

        sum_signal = self._new_signal(f"EventMerge {source.name}: parent-valid sum")
        sum_net: int | None = None
        for index, _parent in enumerate(parents):
            aligned = self._clock_at(source.parents[index].clock, phase)
            converter = ArithmeticCombinator(
                id=self._take_entity_id(),
                operation="+",
                left=Operand(signal=aligned.signal, nets=(aligned.net,)),
                right=Operand(constant=0),
                output_each=False,
                output_signal=sum_signal,
                description=f"EventMerge {source.name}: parent[{index}] valid contribution",
            )
            self.circuit.entities.append(converter)
            self._attach(aligned.net, Endpoint(converter.id, Connector.INPUT))
            endpoint = Endpoint(converter.id, Connector.OUTPUT)
            if sum_net is None:
                sum_net = self._new_net(
                    (sum_signal,),
                    endpoint,
                    label=f"EventMerge {source.name}: summed parent valid",
                )
            else:
                self._attach(sum_net, endpoint)

        assert sum_net is not None
        valid_signal = self._new_signal(f"EventMerge {source.name}: valid")
        normalize = DeciderCombinator(
            id=self._take_entity_id(),
            comparator="!=",
            left=Operand(signal=sum_signal, nets=(sum_net,)),
            right=Operand(constant=0),
            output_signal=valid_signal,
            output_constant=1,
            description=f"EventMerge {source.name}: normalize union valid to one",
        )
        self.circuit.entities.append(normalize)
        self._attach(sum_net, Endpoint(normalize.id, Connector.INPUT))
        valid_net = self._new_net(
            (valid_signal,),
            Endpoint(normalize.id, Connector.OUTPUT),
            label=f"EventMerge {source.name}: derived valid",
        )
        valid_phase = (
            phase
            + FACTORIO_LATENCY.operation_latency("scalar_binary", "event_merge_valid")
            + FACTORIO_LATENCY.operation_latency("compare", "event_merge_valid")
        )
        result = RealizedClock(source.clock.clock_id, valid_signal, valid_net, valid_phase)
        self._clock_sources[source.clock.clock_id] = result
        self._clock_phase_cache[(source.clock.clock_id, valid_phase)] = result
        return result

    def _realize_scalar_merge(self, source: EventMerge) -> RealizedValue:
        cached = self._merge_scalar_payloads.get(source)
        if cached is not None:
            return cached

        gated_payloads: list[RealizedValue] = []
        parent_clocks: list[RealizedClock] = []
        for parent in source.parents:
            event = self._scalar_event(parent)
            payload = self.realize(event)
            parent_clock = self._clock_at(parent.clock, payload.phase)
            gated = self._emit_binary_from_realized("*", payload, parent_clock.as_value())
            gated_payloads.append(gated)
            parent_clocks.append(parent_clock)

        merged = gated_payloads[0]
        for payload in gated_payloads[1:]:
            merged = self._emit_binary_from_realized("+", merged, payload)

        self._merge_scalar_payloads[source] = merged
        valid_input_phase = max(clock.phase for clock in parent_clocks)
        self._realize_merge_valid(source, parent_clocks, valid_input_phase)
        return merged

    def _emit_vector_add(
        self,
        left: RealizedVector,
        right: RealizedVector,
        *,
        description: str,
    ) -> RealizedVector:
        phase = max(left.phase, right.phase)
        left = self.delay_vector_to(left, phase)
        right = self.delay_vector_to(right, phase)
        if left.net != right.net:
            self._add_net_conflict(
                left.net,
                right.net,
                "EventMerge vector add operands must use opposite wire colors",
            )
        entity = ArithmeticCombinator(
            id=self._take_entity_id(),
            operation="+",
            left=Operand(each=True, nets=(left.net,)),
            right=Operand(each=True, nets=(right.net,)),
            output_each=True,
            description=description,
        )
        self.circuit.entities.append(entity)
        input_endpoint = Endpoint(entity.id, Connector.INPUT)
        self._attach(left.net, input_endpoint)
        self._attach(right.net, input_endpoint)
        fixed, dynamic = vector_metadata(self, left.net, right.net)
        output_net = self._new_net(
            (),
            Endpoint(entity.id, Connector.OUTPUT),
            label=description,
            fixed_signals=fixed,
            carries_dynamic_vector=dynamic,
        )
        return RealizedVector(
            output_net,
            phase + FACTORIO_LATENCY.operation_latency("vector_binary", "+"),
        )

    def _realize_vector_merge(self, source: EventMerge) -> RealizedVector:
        cached = self._merge_vector_payloads.get(source)
        if cached is not None:
            return cached

        gated_payloads: list[RealizedVector] = []
        parent_clocks: list[RealizedClock] = []
        for parent in source.parents:
            event = self._vector_event(parent)
            payload = self.realize_vector(event)
            parent_clock = self._clock_at(parent.clock, payload.phase)
            gated, _aligned_valid = self._gate_vector_event(event)
            gated_payloads.append(gated)
            parent_clocks.append(parent_clock)

        merged = gated_payloads[0]
        for index, payload in enumerate(gated_payloads[1:], start=1):
            merged = self._emit_vector_add(
                merged,
                payload,
                description=f"EventMerge {source.name}: add parent[{index}]",
            )

        self._merge_vector_payloads[source] = merged
        valid_input_phase = max(clock.phase for clock in parent_clocks)
        self._realize_merge_valid(source, parent_clocks, valid_input_phase)
        return merged

    def _realize_event_merge(self, source: EventMerge) -> RealizedValue | RealizedVector:
        if source.payload_shape is PayloadShape.SCALAR:
            return self._realize_scalar_merge(source)
        return self._realize_vector_merge(source)

    def _realize_derived_clock(self, source: GateClock | EventMerge) -> RealizedClock:
        if isinstance(source, GateClock):
            return self._realize_gate_clock(source)
        self._realize_event_merge(source)
        return self._clock_sources[source.clock.clock_id]

    def _clock_at(self, clock: Clock, phase: int) -> RealizedClock:
        cached = self._clock_phase_cache.get((clock.clock_id, phase))
        if cached is not None:
            return cached

        source = self._clock_sources.get(clock.clock_id)
        if source is None:
            derived = self._derived_clock_sources.get(clock.clock_id)
            if derived is not None:
                source = self._realize_derived_clock(derived)
        if source is None:
            return super()._clock_at(clock, phase)

        aligned_phase = max(phase, source.phase)
        aligned_key = (clock.clock_id, aligned_phase)
        cached = self._clock_phase_cache.get(aligned_key)
        if cached is not None:
            if phase != aligned_phase:
                self._clock_phase_cache[(clock.clock_id, phase)] = cached
            return cached
        delayed = self.delay_to(source.as_value(), aligned_phase)
        if not isinstance(delayed.signal, int):
            raise AssertionError("derived Event valid token lost its abstract signal identity")
        result = RealizedClock(clock.clock_id, delayed.signal, delayed.net, delayed.phase)
        self._clock_phase_cache[aligned_key] = result
        if phase != aligned_phase:
            self._clock_phase_cache[(clock.clock_id, phase)] = result
        return result

    def _realize_sum_into(self, bridge: SumInto) -> RealizedVector:
        cached = self._sum_into_payloads.get(bridge)
        if cached is not None:
            return cached

        source_payload, _ = self._gate_vector_event(self._vector_event(bridge.source))
        target_valid = self._clock_at(bridge.target.clock, source_payload.phase)
        accumulator_net = source_payload.net
        accumulator = self.net_builders[accumulator_net]
        self._add_net_conflict(
            accumulator_net,
            target_valid.net,
            f"SumInto {bridge.name}: accumulator/target-valid isolation",
        )

        feedback = DeciderCombinator(
            id=self._take_entity_id(),
            comparator="==",
            left=Operand(signal=target_valid.signal, nets=(target_valid.net,)),
            right=Operand(constant=0),
            output_signal=SIGNAL_EVERYTHING,
            output_copy_count_from_input=True,
            copy_count_nets=(accumulator_net,),
            description=f"SumInto {bridge.name}: retain interval sum until target",
        )
        self.circuit.entities.append(feedback)
        feedback_input = Endpoint(feedback.id, Connector.INPUT)
        self._attach(accumulator_net, feedback_input)
        self._attach(target_valid.net, feedback_input)
        self._attach(accumulator_net, Endpoint(feedback.id, Connector.OUTPUT))

        snapshot = DeciderCombinator(
            id=self._take_entity_id(),
            comparator="!=",
            left=Operand(signal=target_valid.signal, nets=(target_valid.net,)),
            right=Operand(constant=0),
            output_signal=SIGNAL_EVERYTHING,
            output_copy_count_from_input=True,
            copy_count_nets=(accumulator_net,),
            description=f"SumInto {bridge.name}: snapshot right-closed interval",
        )
        self.circuit.entities.append(snapshot)
        snapshot_input = Endpoint(snapshot.id, Connector.INPUT)
        self._attach(accumulator_net, snapshot_input)
        self._attach(target_valid.net, snapshot_input)
        snapshot_net = self._new_net(
            accumulator.signals,
            Endpoint(snapshot.id, Connector.OUTPUT),
            label=f"SumInto {bridge.name}: target-clock payload",
            fixed_signals=accumulator.fixed_signals,
            carries_dynamic_vector=accumulator.carries_dynamic_vector,
        )
        snapshot_phase = max(source_payload.phase, target_valid.phase)
        result = RealizedVector(
            snapshot_net,
            snapshot_phase + FACTORIO_LATENCY.state_transition_latency("capture"),
        )
        self._sum_into_payloads[bridge] = result
        return result

    def _freeze_source(self, transition: StateTransition) -> RealizedVector:
        source = super()._freeze_source(transition)
        valid = self._clock_at(transition.clock, source.phase)
        return self.delay_vector_to(source, valid.phase)

    def realize(self, value: ScalarValue) -> RealizedValue:
        if isinstance(value, EventScalarFlow) and isinstance(value.source, GateClock):
            cached = self.memo.get(id(value))
            if cached is not None:
                return cached
            self._event_flow(value)
            result = self._realize_gate_clock(value.source).as_value()
            self.memo[id(value)] = result
            return result
        if isinstance(value, EventScalarFlow) and isinstance(value.source, EventMerge):
            cached = self.memo.get(id(value))
            if cached is not None:
                return cached
            self._event_flow(value)
            result = self._realize_scalar_merge(value.source)
            self.memo[id(value)] = result
            return result
        return super().realize(value)

    def realize_vector(self, value: VectorValue) -> RealizedVector:
        if isinstance(value, EventVectorFlow) and isinstance(value.source, EventMerge):
            cached = self.vector_memo.get(id(value))
            if cached is not None:
                return cached
            self._event_flow(value)
            result = self._realize_vector_merge(value.source)
            self.vector_memo[id(value)] = result
            return result
        return super().realize_vector(value)


def lower_derived_clock_physical(
    module: CircuitModule,
    *,
    state_timing: StateTimingPlan,
) -> AbstractPhysicalCircuit:
    """Lower the supported Event/state/derived-clock subset."""

    return DerivedClockPhysicalLowerer(module, state_timing=state_timing).lower()


__all__ = ["DerivedClockPhysicalLowerer", "lower_derived_clock_physical"]
