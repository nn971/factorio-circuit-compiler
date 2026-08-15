"""Physical lowering for executable clocked/Event flows.

The mature Level lowerer remains the implementation for periodic/stateful Level modules.  This
module brings up a parallel Event lane around one target convention confirmed by in-game probes:
an Event is a payload path plus a one-tick activation/valid pulse.  Payload logic may evaluate
continuously; the activation token is delayed to the physical phase at which the corresponding
logical occurrence is available.

The current slice supports feed-forward external Event clocks, ``SampleOn``, and dense output
materialization with ZERO, VALID, and HOLD policies.  Event state, derived clocks, and stateful
clock bridges remain explicit unsupported cases until their physical cells are added here.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from factorio_circuit.analysis.latency import FACTORIO_LATENCY
from factorio_circuit.analysis.state_timing import StateTimingPlan
from factorio_circuit.events import EventCompilationError
from factorio_circuit.ir.abstract_physical import (
    AbstractNet,
    AbstractPhysicalCircuit,
    ArithmeticCombinator,
    Connector,
    ConstantCombinator,
    DeciderCombinator,
    Endpoint,
    InputPort,
    Operand,
    OutputPort,
    SignalRef,
)
from factorio_circuit.ir.output import (
    OutputMaterializationPolicy,
    output_materializations,
)
from factorio_circuit.ir.physical import SignalId
from factorio_circuit.ir.semantic import (
    CircuitModule,
    Clock,
    ClockId,
    EventScalarFlow,
    EventVectorFlow,
    Flow,
    InputSample,
    PayloadShape,
    SampleOn,
    ScalarValue,
    TemporalModality,
    VectorBinaryOp,
    VectorFilter,
    VectorInputSample,
    VectorScalarOp,
    VectorSelect,
    VectorValue,
    is_vector_value,
    validate_expression_flow,
)
from factorio_circuit.lowering.ir_to_abstract_physical import (
    AbstractPhysicalLowerer,
    RealizedValue,
    RealizedVector,
    _NetBuilder,
)
from factorio_circuit.lowering.vector_binary import realize_vector_binary
from factorio_circuit.lowering.vector_select import realize_vector_select
from factorio_circuit.lowering.vector_unary import realize_vector_filter, realize_vector_scalar
from factorio_circuit.target.factorio.signals import SIGNAL_EVERYTHING


@dataclass(frozen=True, slots=True)
class RealizedClock:
    """One physical activation token at a known latency from a semantic clock occurrence."""

    clock_id: ClockId
    signal: int
    net: int
    phase: int

    def as_value(self) -> RealizedValue:
        return RealizedValue(self.signal, self.net, self.phase)


class ClockedPhysicalLowerer(AbstractPhysicalLowerer):
    """Lower the supported clocked-flow subset onto the Abstract Physical IR.

    ``AbstractPhysicalLowerer.__init__`` deliberately rejects Event modules, so this subclass owns a
    small initialization shim while reusing its scalar realization, delay, net, and allocation
    machinery.  Packing is disabled in this lane until clock-aware packing can prove that a packed
    group shares one activation token.
    """

    def __init__(self, module: CircuitModule, *, state_timing: StateTimingPlan) -> None:
        self.module = module
        self.enable_packing = False
        self.state_timing = state_timing
        self.circuit = AbstractPhysicalCircuit(name=module.name)
        self.next_entity_id = 1
        self.next_signal_id = 1
        self.next_net_id = 1
        self.memo: dict[int, RealizedValue] = {}
        self.vector_memo: dict[int, RealizedVector] = {}
        self.state_outputs: dict[str, RealizedVector] = {}
        self.state_memory_ids: dict[str, int] = {}
        self.state_memory_nets: dict[str, int] = {}
        self.delay_cache: dict[tuple[int, SignalRef, int], RealizedValue] = {}
        self.net_builders: dict[int, _NetBuilder] = {}
        self.signal_conflict_keys: set[tuple[int, int]] = set()
        self.signal_alias_keys: set[tuple[int, int]] = set()
        self.net_conflict_keys: set[tuple[int, int]] = set()
        self.use_count: Counter[int] = Counter()
        self.partition_for_op = {}
        self.pairwise_partition_for_op = {}
        self.shared_selects_by_condition = {}
        self.output_value_ids = {id(value) for value in module.output.values}

        self._event_scalar_payloads: dict[object, RealizedValue] = {}
        self._event_vector_payloads: dict[object, RealizedVector] = {}
        self._clock_sources: dict[ClockId, RealizedClock] = {}
        self._clock_phase_cache: dict[tuple[ClockId, int], RealizedClock] = {}

    def lower(self) -> AbstractPhysicalCircuit:
        self._check_clocked_scope()
        self._create_input_markers()
        self._create_event_input_markers()
        self._create_materialized_outputs()
        self.circuit.nets = [
            AbstractNet(
                id=net_id,
                signals=builder.signals,
                endpoints=tuple(builder.endpoints),
                label=builder.label,
                fixed_signals=builder.fixed_signals,
                carries_dynamic_vector=builder.carries_dynamic_vector,
            )
            for net_id, builder in sorted(self.net_builders.items())
        ]
        self.circuit.validate()
        return self.circuit

    def _check_clocked_scope(self) -> None:
        if (
            self.module.state_registers
            or self.module.state_operations
            or self.module.event_state_operations
        ):
            raise EventCompilationError(
                "physical Event lowering currently supports feed-forward flows only; "
                "event-clocked state is the next lowering slice"
            )
        if self.module.transitions:
            triggered = [transition for transition in self.module.transitions if transition.trigger]
            if triggered:
                raise EventCompilationError(
                    "physical Event lowering has not yet realized event-clocked state transitions"
                )
        if self.state_timing.unsupported_crossings:
            raise EventCompilationError(
                "physical Event lowering encountered an unsupported cross-clock state dependency"
            )

    def _create_event_input_markers(self) -> None:
        occupied_names = {port.name for port in self.circuit.inputs}
        for source in self.module.event_inputs:
            payload_name = source.name
            valid_name = f"{source.name}__valid"
            if payload_name in occupied_names or valid_name in occupied_names:
                raise EventCompilationError(
                    f"physical Event ABI port name collision for {source.name!r}; "
                    f"{valid_name!r} is reserved as its activation input"
                )
            occupied_names.update((payload_name, valid_name))

            payload_marker = ConstantCombinator(
                id=self._take_entity_id(),
                description=f"EVENT INPUT {source.name} — payload",
                annotation_only=True,
            )
            self.circuit.entities.append(payload_marker)
            payload_endpoint = Endpoint(payload_marker.id, Connector.SINGLE)
            if source.payload_shape is PayloadShape.SCALAR:
                payload_signal = self._new_signal(f"event {source.name}: payload")
                payload_net = self._new_net(
                    (payload_signal,),
                    payload_endpoint,
                    label=f"event input {source.name}: payload",
                )
                self.circuit.inputs.append(
                    InputPort(payload_name, payload_endpoint, payload_signal)
                )
                self._event_scalar_payloads[source] = RealizedValue(
                    payload_signal,
                    payload_net,
                    0,
                )
            else:
                payload_net = self._new_net(
                    (),
                    payload_endpoint,
                    label=f"event input {source.name}: vector payload",
                    carries_dynamic_vector=True,
                )
                self.circuit.inputs.append(InputPort(payload_name, payload_endpoint, None))
                self._event_vector_payloads[source] = RealizedVector(payload_net, 0)

            valid_signal = self._new_signal(f"event {source.name}: valid")
            valid_marker = ConstantCombinator(
                id=self._take_entity_id(),
                description=f"EVENT INPUT {source.name} — one-tick valid pulse",
                annotation_only=True,
            )
            self.circuit.entities.append(valid_marker)
            valid_endpoint = Endpoint(valid_marker.id, Connector.SINGLE)
            valid_net = self._new_net(
                (valid_signal,),
                valid_endpoint,
                label=f"event input {source.name}: valid",
            )
            self.circuit.inputs.append(InputPort(valid_name, valid_endpoint, valid_signal))
            clock_id = source.clock.clock_id
            if clock_id in self._clock_sources:
                raise EventCompilationError(
                    f"multiple external Event inputs declare structural clock {clock_id.identity!r}"
                )
            realized_clock = RealizedClock(clock_id, valid_signal, valid_net, 0)
            self._clock_sources[clock_id] = realized_clock
            self._clock_phase_cache[(clock_id, 0)] = realized_clock

    @staticmethod
    def _event_flow(value: object) -> Flow:
        flow = getattr(value, "flow", None)
        if not isinstance(flow, Flow) or flow.modality is not TemporalModality.EVENT:
            raise EventCompilationError(
                f"{type(value).__name__} does not carry canonical Event Flow metadata"
            )
        if flow.logical_offset != 0:
            raise EventCompilationError(
                "nonzero Event occurrence offsets require an explicit temporal buffer; "
                "physical lowering never interprets .step() as a game-tick delay"
            )
        return flow

    def _clock_at(self, clock: Clock, phase: int) -> RealizedClock:
        key = (clock.clock_id, phase)
        cached = self._clock_phase_cache.get(key)
        if cached is not None:
            return cached
        source = self._clock_sources.get(clock.clock_id)
        if source is None:
            raise EventCompilationError(
                f"physical lowering for derived clock {clock.identity!r} is not implemented yet"
            )
        delayed = self.delay_to(source.as_value(), phase)
        if not isinstance(delayed.signal, int):  # pragma: no cover - valid signals are abstract
            raise AssertionError("physical Event valid token lost its abstract signal identity")
        result = RealizedClock(clock.clock_id, delayed.signal, delayed.net, delayed.phase)
        self._clock_phase_cache[key] = result
        return result

    def realize(self, value: ScalarValue) -> RealizedValue:
        cached = self.memo.get(id(value))
        if cached is not None:
            return cached
        if isinstance(value, EventScalarFlow):
            self._event_flow(value)
            try:
                result = self._event_scalar_payloads[value.source]
            except KeyError as exc:
                raise EventCompilationError(
                    f"Event source {value.source.name!r} is not a declared external input"
                ) from exc
            self.memo[id(value)] = result
            return result
        if isinstance(value, SampleOn):
            flow = self._event_flow(value)
            if is_vector_value(value.source):
                raise TypeError("vector SampleOn must be realized through realize_vector()")
            if flow.logical_offset != 0:  # kept local for a clearer future extension point
                raise EventCompilationError("SampleOn requires the current target occurrence")
            result = self.realize(value.source)  # type: ignore[arg-type]
            self.memo[id(value)] = result
            return result
        if isinstance(value, InputSample) and value.offset != 0:
            raise EventCompilationError(
                "nonzero Level sample offsets inside an Event module need clock normalization first"
            )
        return super().realize(value)

    def realize_vector(self, value: VectorValue) -> RealizedVector:
        cached = self.vector_memo.get(id(value))
        if cached is not None:
            return cached
        if isinstance(value, EventVectorFlow):
            self._event_flow(value)
            try:
                result = self._event_vector_payloads[value.source]
            except KeyError as exc:
                raise EventCompilationError(
                    f"Event source {value.source.name!r} is not a declared external input"
                ) from exc
        elif isinstance(value, SampleOn):
            self._event_flow(value)
            if not is_vector_value(value.source):
                raise TypeError("scalar SampleOn must be realized through realize()")
            result = self.realize_vector(value.source)  # type: ignore[arg-type]
        elif isinstance(value, VectorInputSample) and value.offset != 0:
            raise EventCompilationError(
                "nonzero Level vector sample offsets inside an Event module need "
                "clock normalization"
            )
        elif isinstance(value, VectorBinaryOp):
            result = realize_vector_binary(self, value)
        elif isinstance(value, VectorScalarOp):
            result = realize_vector_scalar(self, value)
        elif isinstance(value, VectorSelect):
            result = realize_vector_select(self, value)
        elif isinstance(value, VectorFilter):
            result = realize_vector_filter(self, value)
        else:
            return super().realize_vector(value)
        self.vector_memo[id(value)] = result
        return result

    def _event_clock_for(self, value: object) -> Clock:
        flow = self._event_flow(value)
        facts = validate_expression_flow(value)
        if facts.modality is not TemporalModality.EVENT or facts.clock is None:
            raise EventCompilationError("physical Event output lost its occurrence clock")
        if facts.clock != flow.clock:
            raise EventCompilationError(
                "Event Flow clock disagrees with recursive expression clock"
            )
        return facts.clock

    def _gate_scalar_event(self, value: ScalarValue) -> tuple[RealizedValue, RealizedClock]:
        payload = self.realize(value)
        clock = self._event_clock_for(value)
        valid = self._clock_at(clock, payload.phase)
        gated = self._emit_binary_from_realized("*", payload, valid.as_value())
        aligned_valid = self._clock_at(clock, gated.phase)
        return gated, aligned_valid

    def _gate_vector_event(self, value: VectorValue) -> tuple[RealizedVector, RealizedClock]:
        payload = self.realize_vector(value)
        clock = self._event_clock_for(value)
        valid = self._clock_at(clock, payload.phase)
        valid_value = valid.as_value()
        payload = self.delay_vector_to(payload, valid.phase)
        self._add_net_conflict(
            payload.net,
            valid.net,
            "Event vector payload and valid token must use separate wire networks",
        )
        source = self.net_builders[payload.net]
        entity = ArithmeticCombinator(
            id=self._take_entity_id(),
            operation="*",
            left=Operand(each=True, nets=(payload.net,)),
            right=Operand(signal=valid_value.signal, nets=(valid_value.net,)),
            output_each=True,
            description="Event output: zero when invalid",
        )
        self.circuit.entities.append(entity)
        endpoint = Endpoint(entity.id, Connector.INPUT)
        self._attach(payload.net, endpoint)
        self._attach(valid.net, endpoint)
        output_net = self._new_net(
            source.signals,
            Endpoint(entity.id, Connector.OUTPUT),
            label="materialized Event vector payload",
            fixed_signals=source.fixed_signals,
            carries_dynamic_vector=source.carries_dynamic_vector,
        )
        phase = payload.phase + FACTORIO_LATENCY.operation_latency("vector_scalar", "event_gate")
        gated = RealizedVector(output_net, phase)
        return gated, self._clock_at(clock, phase)

    def _hold_scalar_event(self, value: ScalarValue) -> RealizedValue:
        payload = self.realize(value)
        clock = self._event_clock_for(value)
        valid = self._clock_at(clock, payload.phase)
        self._add_net_conflict(
            payload.net,
            valid.net,
            "Event HOLD payload and valid token must use separate wire networks",
        )

        update = DeciderCombinator(
            id=self._take_entity_id(),
            comparator="!=",
            left=Operand(signal=valid.signal, nets=(valid.net,)),
            right=Operand(constant=0),
            output_signal=payload.signal,
            output_copy_count_from_input=True,
            copy_count_nets=(payload.net,),
            description="Event HOLD: capture scalar payload when valid",
        )
        self.circuit.entities.append(update)
        update_input = Endpoint(update.id, Connector.INPUT)
        self._attach(payload.net, update_input)
        self._attach(valid.net, update_input)

        if isinstance(payload.signal, int):
            memory_signals: tuple[int, ...] = (payload.signal,)
            memory_fixed_signals: tuple[SignalId, ...] = ()
        else:
            memory_signals = ()
            memory_fixed_signals = (payload.signal,)
        memory_net = self._new_net(
            memory_signals,
            Endpoint(update.id, Connector.OUTPUT),
            label="Event HOLD scalar memory",
            fixed_signals=memory_fixed_signals,
        )

        feedback = DeciderCombinator(
            id=self._take_entity_id(),
            comparator="==",
            left=Operand(signal=valid.signal, nets=(valid.net,)),
            right=Operand(constant=0),
            output_signal=payload.signal,
            output_copy_count_from_input=True,
            copy_count_nets=(memory_net,),
            description="Event HOLD: retain scalar payload while invalid",
        )
        self.circuit.entities.append(feedback)
        feedback_input = Endpoint(feedback.id, Connector.INPUT)
        self._attach(memory_net, feedback_input)
        self._attach(valid.net, feedback_input)
        self._attach(memory_net, Endpoint(feedback.id, Connector.OUTPUT))
        self._add_net_conflict(
            memory_net,
            valid.net,
            "Event HOLD memory and valid token must use separate wire networks",
        )

        return RealizedValue(
            payload.signal,
            memory_net,
            payload.phase + FACTORIO_LATENCY.state_transition_latency("capture"),
        )

    def _hold_vector_event(self, value: VectorValue) -> RealizedVector:
        payload = self.realize_vector(value)
        clock = self._event_clock_for(value)
        valid = self._clock_at(clock, payload.phase)
        source = self.net_builders[payload.net]
        self._add_net_conflict(
            payload.net,
            valid.net,
            "Event HOLD vector payload and valid token must use separate wire networks",
        )

        update = DeciderCombinator(
            id=self._take_entity_id(),
            comparator="!=",
            left=Operand(signal=valid.signal, nets=(valid.net,)),
            right=Operand(constant=0),
            output_signal=SIGNAL_EVERYTHING,
            output_copy_count_from_input=True,
            copy_count_nets=(payload.net,),
            description="Event HOLD: capture vector payload when valid",
        )
        self.circuit.entities.append(update)
        update_input = Endpoint(update.id, Connector.INPUT)
        self._attach(payload.net, update_input)
        self._attach(valid.net, update_input)

        memory_net = self._new_net(
            source.signals,
            Endpoint(update.id, Connector.OUTPUT),
            label="Event HOLD vector memory",
            fixed_signals=source.fixed_signals,
            carries_dynamic_vector=source.carries_dynamic_vector,
        )
        feedback = DeciderCombinator(
            id=self._take_entity_id(),
            comparator="==",
            left=Operand(signal=valid.signal, nets=(valid.net,)),
            right=Operand(constant=0),
            output_signal=SIGNAL_EVERYTHING,
            output_copy_count_from_input=True,
            copy_count_nets=(memory_net,),
            description="Event HOLD: retain vector payload while invalid",
        )
        self.circuit.entities.append(feedback)
        feedback_input = Endpoint(feedback.id, Connector.INPUT)
        self._attach(memory_net, feedback_input)
        self._attach(valid.net, feedback_input)
        self._attach(memory_net, Endpoint(feedback.id, Connector.OUTPUT))
        self._add_net_conflict(
            memory_net,
            valid.net,
            "Event HOLD vector memory and valid token must use separate wire networks",
        )

        return RealizedVector(
            memory_net,
            payload.phase + FACTORIO_LATENCY.state_transition_latency("capture"),
        )

    def _create_materialized_outputs(self) -> None:
        contracts = output_materializations(self.module.output)
        for index, value in enumerate(self.module.output.values):
            declared_name = self.module.output.names[index] if self.module.output.names else None
            name = declared_name or getattr(value, "name", None) or f"out{index}"
            facts = validate_expression_flow(value)
            if facts.modality is not TemporalModality.EVENT:
                realized = (
                    self.realize_vector(value)  # type: ignore[arg-type]
                    if is_vector_value(value)
                    else self.realize(value)  # type: ignore[arg-type]
                )
                self._add_output_marker(name, realized)
                continue

            contract = contracts[index]
            if contract.policy is OutputMaterializationPolicy.HOLD:
                hold_payload = (
                    self._hold_vector_event(value)  # type: ignore[arg-type]
                    if is_vector_value(value)
                    else self._hold_scalar_event(value)  # type: ignore[arg-type]
                )
                self._add_output_marker(name, hold_payload)
                continue

            payload: RealizedValue | RealizedVector
            if is_vector_value(value):
                payload, valid = self._gate_vector_event(value)  # type: ignore[arg-type]
            else:
                payload, valid = self._gate_scalar_event(value)  # type: ignore[arg-type]
            self._add_output_marker(name, payload)
            if contract.policy is OutputMaterializationPolicy.VALID:
                valid_name = contract.valid_name or f"{name}__valid"
                self._add_output_marker(valid_name, valid.as_value())

    def _add_output_marker(self, name: str, realized: RealizedValue | RealizedVector) -> None:
        if isinstance(realized, RealizedVector):
            description = f"OUTPUT {name} — whole signal vector"
            signal = None
            phase = realized.phase
        else:
            description = f"OUTPUT {name} — phase +{realized.phase} tick(s)"
            signal = realized.signal
            phase = realized.phase
        marker = ConstantCombinator(
            id=self._take_entity_id(),
            description=description,
            annotation_only=True,
        )
        self.circuit.entities.append(marker)
        endpoint = Endpoint(marker.id, Connector.SINGLE)
        self._attach(realized.net, endpoint)
        self.circuit.outputs.append(OutputPort(name, endpoint, signal, phase))


def lower_clocked_physical(
    module: CircuitModule,
    *,
    state_timing: StateTimingPlan,
) -> AbstractPhysicalCircuit:
    """Lower the currently supported clocked/Event target slice."""

    return ClockedPhysicalLowerer(module, state_timing=state_timing).lower()


__all__ = ["ClockedPhysicalLowerer", "RealizedClock", "lower_clocked_physical"]
