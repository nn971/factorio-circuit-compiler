"""Physical realization of flow-local Event occurrence reindexing.

A positive Event ``.step(n)`` is not a game-tick delay and does not predict a future payload.  The
reference kernel defines it as the tail of the same occurrence stream: suppress the first ``n``
activations, then preserve every later activation and its current payload.  This layer realizes that
startup-only operation on top of the bridge-aware Event lowerer.

One backend-local occurrence counter is shared by all offsets of a semantic clock.  Each nonzero
offset gets a threshold/latch and a valid gate.  Payload logic remains speculative and is merely
phase-aligned with the gated valid token by the established Event lowering path.
"""

from __future__ import annotations

from dataclasses import replace

from factorio_circuit.analysis.latency import FACTORIO_LATENCY
from factorio_circuit.analysis.state_timing import StateTimingPlan
from factorio_circuit.events import EventCompilationError
from factorio_circuit.ir.abstract_physical import (
    AbstractPhysicalCircuit,
    ArithmeticCombinator,
    Connector,
    DeciderCombinator,
    Endpoint,
    Operand,
)
from factorio_circuit.ir.semantic import (
    CircuitModule,
    Clock,
    ClockId,
    ClockProvenance,
    EventInput,
    Flow,
    TemporalModality,
)
from factorio_circuit.ir.state import StateTransition
from factorio_circuit.lowering.clock_bridge_physical import ClockBridgePhysicalLowerer
from factorio_circuit.lowering.clocked_physical import RealizedClock

_I32_MAX = 2**31 - 1


class OccurrenceReindexPhysicalLowerer(ClockBridgePhysicalLowerer):
    """Bridge-aware Event lowerer with startup occurrence-prefix suppression."""

    def __init__(self, module: CircuitModule, *, state_timing: StateTimingPlan) -> None:
        self._tail_clock_index: dict[tuple[ClockId, int], Clock] = {}
        self._tail_clock_specs: dict[ClockId, tuple[Clock, int]] = {}
        self._occurrence_counters: dict[ClockId, tuple[int, int]] = {}
        super().__init__(module, state_timing=state_timing)

        # Canonical analysis keeps the base clock plus a logical occurrence offset. The physical
        # state cells expect a clock token directly, so project that pair onto a backend-only tail
        # trigger/clock while leaving the semantic module and timing plan untouched.
        self._event_transitions = tuple(
            self._physical_transition(transition) for transition in self._event_transitions
        )

    @staticmethod
    def _event_flow(value: object) -> Flow:
        flow = getattr(value, "flow", None)
        if not isinstance(flow, Flow) or flow.modality is not TemporalModality.EVENT:
            raise EventCompilationError(
                f"{type(value).__name__} does not carry canonical Event Flow metadata"
            )
        if flow.logical_offset < 0:
            raise EventCompilationError("Event occurrence offsets must be non-negative")
        return flow

    def _tail_clock_for(self, clock: Clock, logical_offset: int) -> Clock:
        if logical_offset == 0:
            return clock
        if logical_offset < 0:
            raise EventCompilationError("Event occurrence offsets must be non-negative")
        if logical_offset > _I32_MAX:
            raise EventCompilationError(
                "physical Event occurrence offsets are limited to signed 32-bit counts"
            )
        key = (clock.clock_id, logical_offset)
        cached = self._tail_clock_index.get(key)
        if cached is not None:
            return cached
        # StateTransition requires Event trigger clocks to match structurally, and EventInput
        # requires EXTERNAL_EVENT provenance.  This clock is backend-local only: it denotes the
        # externally driven base stream after startup prefix suppression, not a new semantic clock.
        tail = Clock(
            identity=(
                f"\0physical-tail:{clock.provenance.value}:{clock.identity}:{logical_offset}"
            ),
            provenance=ClockProvenance.EXTERNAL_EVENT,
            contract=clock.contract,
        )
        self._tail_clock_index[key] = tail
        self._tail_clock_specs[tail.clock_id] = (clock, logical_offset)
        return tail

    def _physical_transition(self, transition: StateTransition) -> StateTransition:
        if transition.logical_offset == 0:
            return transition
        tail_clock = self._tail_clock_for(transition.clock, transition.logical_offset)
        tail_trigger = (
            EventInput(
                name=(
                    f"\0physical-tail-trigger:{transition.trigger.name}:{transition.logical_offset}"
                ),
                payload_shape=transition.trigger.payload_shape,
                clock=tail_clock,
            )
            if transition.trigger is not None
            else None
        )
        return replace(
            transition,
            clock=tail_clock,
            trigger=tail_trigger,
            logical_offset=0,
        )

    def _event_clock_for(self, value: object) -> Clock:
        base = super()._event_clock_for(value)
        flow = self._event_flow(value)
        return self._tail_clock_for(base, flow.logical_offset)

    def _counter_for(self, clock: Clock, source: RealizedClock) -> tuple[int, int]:
        cached = self._occurrence_counters.get(clock.clock_id)
        if cached is not None:
            return cached

        signal = self._new_signal(f"Event clock {clock.identity}: occurrence count")
        entity_id = self._take_entity_id()
        input_endpoint = Endpoint(entity_id, Connector.INPUT)
        output_endpoint = Endpoint(entity_id, Connector.OUTPUT)
        net = self._new_net(
            (signal,),
            input_endpoint,
            label=f"Event clock {clock.identity}: occurrence counter",
        )
        self._attach(net, output_endpoint)
        self._attach(source.net, input_endpoint)
        self._add_net_conflict(
            net,
            source.net,
            f"Event clock {clock.identity}: count/valid isolation",
        )
        counter = ArithmeticCombinator(
            id=entity_id,
            operation="+",
            left=Operand(signal=signal, nets=(net,)),
            right=Operand(signal=source.signal, nets=(source.net,)),
            output_each=False,
            output_signal=signal,
            description=f"Event clock {clock.identity}: count valid occurrences",
        )
        self.circuit.entities.append(counter)
        self._occurrence_counters[clock.clock_id] = (signal, net)
        return signal, net

    def _realize_tail_clock(self, tail: Clock) -> RealizedClock:
        cached = self._clock_sources.get(tail.clock_id)
        if cached is not None:
            return cached
        try:
            base_clock, logical_offset = self._tail_clock_specs[tail.clock_id]
        except KeyError as exc:  # pragma: no cover - backend-only clocks are created above
            raise EventCompilationError("unknown physical Event tail clock") from exc

        base = super()._clock_at(base_clock, 0)
        count_signal, count_net = self._counter_for(base_clock, base)
        ready_signal = self._new_signal(
            f"Event clock {base_clock.identity}: tail +{logical_offset} ready"
        )

        # Once the counter reaches n, seed a one-bit latch. The latch makes the tail permanent even
        # if the signed counter eventually wraps after another 2^31 occurrences.
        threshold = DeciderCombinator(
            id=self._take_entity_id(),
            comparator=">=",
            left=Operand(signal=count_signal, nets=(count_net,)),
            right=Operand(constant=logical_offset),
            output_signal=ready_signal,
            output_constant=1,
            description=(
                f"Event clock {base_clock.identity}: enable tail after {logical_offset} occurrences"
            ),
        )
        self.circuit.entities.append(threshold)
        self._attach(count_net, Endpoint(threshold.id, Connector.INPUT))
        ready_net = self._new_net(
            (ready_signal,),
            Endpoint(threshold.id, Connector.OUTPUT),
            label=f"Event clock {base_clock.identity}: tail +{logical_offset} ready",
        )

        latch = DeciderCombinator(
            id=self._take_entity_id(),
            comparator="!=",
            left=Operand(signal=ready_signal, nets=(ready_net,)),
            right=Operand(constant=0),
            output_signal=ready_signal,
            output_constant=1,
            description=(
                f"Event clock {base_clock.identity}: latch tail +{logical_offset} readiness"
            ),
        )
        self.circuit.entities.append(latch)
        self._attach(ready_net, Endpoint(latch.id, Connector.INPUT))
        self._attach(ready_net, Endpoint(latch.id, Connector.OUTPUT))

        # Threshold readiness for occurrence k is available one tick after base valid(k). Delay the
        # same valid pulse by one physical stage, then conditionally copy it. The resulting token is
        # therefore exactly the same occurrence stream with its first n activations removed.
        delayed_valid = self.delay_to(base.as_value(), base.phase + 1)
        if not isinstance(delayed_valid.signal, int):  # pragma: no cover - Event valid is abstract
            raise AssertionError("Event valid token lost its abstract signal identity")
        self._add_net_conflict(
            ready_net,
            delayed_valid.net,
            f"Event clock {base_clock.identity}: tail-ready/valid isolation",
        )
        gate = DeciderCombinator(
            id=self._take_entity_id(),
            comparator="!=",
            left=Operand(signal=ready_signal, nets=(ready_net,)),
            right=Operand(constant=0),
            output_signal=delayed_valid.signal,
            output_copy_count_from_input=True,
            copy_count_nets=(delayed_valid.net,),
            description=(
                f"Event clock {base_clock.identity}: suppress first {logical_offset} occurrences"
            ),
        )
        self.circuit.entities.append(gate)
        gate_input = Endpoint(gate.id, Connector.INPUT)
        self._attach(ready_net, gate_input)
        self._attach(delayed_valid.net, gate_input)
        output_net = self._new_net(
            (delayed_valid.signal,),
            Endpoint(gate.id, Connector.OUTPUT),
            label=f"Event clock {base_clock.identity}: tail +{logical_offset} valid",
        )
        phase = delayed_valid.phase + FACTORIO_LATENCY.operation_latency(
            "compare", "event_occurrence_tail"
        )
        result = RealizedClock(tail.clock_id, delayed_valid.signal, output_net, phase)
        self._clock_sources[tail.clock_id] = result
        self._clock_phase_cache[(tail.clock_id, phase)] = result
        return result

    def _clock_at(self, clock: Clock, phase: int) -> RealizedClock:
        if clock.clock_id in self._tail_clock_specs and clock.clock_id not in self._clock_sources:
            self._realize_tail_clock(clock)
        return super()._clock_at(clock, phase)


def lower_occurrence_reindexed_physical(
    module: CircuitModule,
    *,
    state_timing: StateTimingPlan,
) -> AbstractPhysicalCircuit:
    """Lower Event modules with bridge alignment and flow-local occurrence reindexing."""

    return OccurrenceReindexPhysicalLowerer(module, state_timing=state_timing).lower()


__all__ = ["OccurrenceReindexPhysicalLowerer", "lower_occurrence_reindexed_physical"]
