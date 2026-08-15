"""Semantic/reference simulation for the Phase 3 Event vertical slice.

This path is intentionally separate from the ordinary Level stream simulator.  It models only
deterministic Event occurrences, same-timestamp snapshots, and FreezeReg captures; it does not
model physical pulses, buffering, output streams, or bridges.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol, cast

from factorio_circuit.analysis.state_timing import analyze_clocked_timing, validate_event_throughput
from factorio_circuit.events import (
    EventCausalityError,
    EventCrossingError,
    EventMaterializationError,
    EventScheduleError,
    EventThroughputError,
)
from factorio_circuit.ir.physical import SignalId
from factorio_circuit.ir.semantic import (
    BinaryOp,
    CircuitModule,
    Clock,
    ClockContractEnvironment,
    Compare,
    Constant,
    EventInput,
    EventScalarFlow,
    EventVectorFlow,
    Flow,
    FlowInput,
    FlowInputSample,
    FlowVectorInput,
    FlowVectorInputSample,
    Input,
    InputSample,
    PayloadShape,
    SampleOn,
    ScalarValue,
    Select,
    TemporalModality,
    VectorBinaryOp,
    VectorConstant,
    VectorFilter,
    VectorInput,
    VectorInputSample,
    VectorScalarOp,
    VectorSelect,
    VectorSignal,
    VectorValue,
    is_vector_value,
)
from factorio_circuit.ir.state import (
    AccumulatorRegister,
    FreezeCapture,
    FreezeRegister,
    StateRegister,
    StateTransition,
    VectorRegisterRead,
    state_transitions,
)
from factorio_circuit.simulate.kernel import (
    EvaluationContext,
    evaluate_scalar,
    evaluate_vector,
    run_timestamp_kernel,
)
from factorio_circuit.target.factorio.semantics import i32

SignalMap = dict[SignalId, int]
EventPayload = int | SignalMap
LevelRow = dict[str, object]
StateSnapshot = dict[str, SignalMap]
ANYTHING_SIGNAL = SignalId("virtual", "signal-anything")


class EventSourceHandle(Protocol):
    @property
    def ir(self) -> EventInput: ...


@dataclass(frozen=True, slots=True)
class EventOccurrence:
    """One present Event occurrence; zero and empty-vector payloads are still present."""

    timestamp: int
    payload: object

    def __post_init__(self) -> None:
        if isinstance(self.timestamp, bool) or not isinstance(self.timestamp, int):
            raise EventScheduleError("Event occurrence timestamps must be non-boolean integers")
        if self.timestamp < 0:
            raise EventScheduleError("Event occurrence timestamps must be non-negative")
        if self.payload is None:
            raise EventScheduleError("None is not a valid Event payload or absence marker")


@dataclass(frozen=True, slots=True, init=False)
class EventSchedule:
    """The complete per-source schedule supplied to :func:`simulate_events`."""

    source: EventInput
    occurrences: tuple[EventOccurrence, ...]

    def __init__(
        self,
        source: EventInput | EventSourceHandle,
        occurrences: Sequence[EventOccurrence],
    ) -> None:
        if isinstance(source, EventInput):
            normalized_source = source
        else:
            try:
                normalized_source = source.ir
            except AttributeError as exc:
                raise EventScheduleError(
                    "EventSchedule source must be a declared EventInput"
                ) from exc
        if not isinstance(normalized_source, EventInput):
            raise EventScheduleError("EventSchedule source must be a declared EventInput")
        try:
            normalized_occurrences = tuple(occurrences)
        except TypeError as exc:
            raise EventScheduleError("EventSchedule occurrences must be iterable") from exc
        if any(not isinstance(item, EventOccurrence) for item in normalized_occurrences):
            raise EventScheduleError("EventSchedule occurrences must be EventOccurrence values")
        object.__setattr__(self, "source", normalized_source)
        object.__setattr__(self, "occurrences", normalized_occurrences)


@dataclass(frozen=True, slots=True)
class EventActivation:
    """One declaration-ordered activation inside a timestamp reaction."""

    source: EventInput
    payload: EventPayload
    captured_registers: tuple[str, ...]
    crossing_observations: tuple[SampleOnObservation, ...] = ()


@dataclass(frozen=True, slots=True)
class SampleOnObservation:
    """One declaration-ordered Level snapshot observed by an Event activation."""

    crossing: SampleOn
    value: EventPayload


@dataclass(frozen=True, slots=True)
class EventReaction:
    """One atomic timestamp reaction and its shared snapshots."""

    timestamp: int
    activations: tuple[EventActivation, ...]
    level_row: LevelRow
    state_before: StateSnapshot
    state_after: StateSnapshot


@dataclass(frozen=True, slots=True, kw_only=True)
class TimestampDomain:
    """An immutable half-open timestamp interval ``[start, stop)``."""

    start: int = 0
    stop: int

    def __post_init__(self) -> None:
        for name, value in (("start", self.start), ("stop", self.stop)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise EventScheduleError(f"timestamp domain {name} must be an integer")
        if self.start < 0:
            raise EventScheduleError("timestamp domain start must be non-negative")
        if self.stop < self.start:
            raise EventScheduleError("timestamp domain stop must be >= start")


@dataclass(frozen=True, slots=True)
class EventSimulationResult:
    """Final Event-driven state, reactions, and their explicit timestamp domain."""

    final_state: StateSnapshot
    reactions: tuple[EventReaction, ...]
    domain: TimestampDomain
    event_inputs: tuple[EventInput, ...] = ()
    sample_on_crossings: tuple[SampleOn, ...] = ()


class EventMaterializationPolicy(StrEnum):
    """How an Event-shaped semantic trace behaves between present occurrences."""

    HOLD = "hold"
    ZERO = "zero"
    VALID = "valid"


@dataclass(frozen=True, slots=True)
class MaterializedEventTrace:
    """A semantic/reference-only timestamped Event or SampleOn trace."""

    reference: object
    payload_shape: PayloadShape
    policy: EventMaterializationPolicy
    domain: TimestampDomain
    payloads: tuple[EventPayload, ...]
    valid: tuple[bool, ...] | None = None


def simulate_events(
    module: CircuitModule,
    level_stream: Sequence[Mapping[str, object]],
    schedules: Sequence[EventSchedule],
    *,
    stop_timestamp: int | None = None,
) -> EventSimulationResult:
    """Run semantic Event captures against dense Level snapshots.

    Every declared source must have exactly one schedule, including an empty schedule.  Occurrences
    sharing a timestamp observe one old-state/Level snapshot and commit together.
    """

    from factorio_circuit.lowering.frontend_to_ir import normalize_module

    module = normalize_module(module)
    try:
        clock_environment = ClockContractEnvironment.from_module(module)
    except ValueError as exc:
        raise EventScheduleError("conflicting Event clock contract declarations") from exc
    _validate_sample_on_crossings(module)
    schedules_by_source = _validate_schedules(module, schedules, clock_environment)
    max_occurrence = max(
        (
            occurrence.timestamp
            for values in schedules_by_source.values()
            for occurrence, _ in values
        ),
        default=-1,
    )
    if stop_timestamp is None:
        stop_timestamp = max(len(level_stream), max_occurrence + 1)
    elif (
        isinstance(stop_timestamp, bool)
        or not isinstance(stop_timestamp, int)
        or stop_timestamp < 0
    ):
        raise EventScheduleError("stop_timestamp must be a non-negative integer or None")
    if max_occurrence >= stop_timestamp:
        raise EventScheduleError("Event occurrences must lie in the half-open simulation domain")
    transitions_by_source = _validate_event_module(module, clock_environment)
    timing = analyze_clocked_timing(module, clock_environment=clock_environment)
    validate_event_throughput(timing, clock_environment=clock_environment)
    initial_state: StateSnapshot = {register.name: {} for register in module.state_registers}
    declaration_order = {source: index for index, source in enumerate(module.event_inputs)}
    crossings_by_target: dict[EventInput, tuple[SampleOn, ...]] = {
        source: tuple(
            crossing for crossing in module.sample_on_crossings if crossing.target == source
        )
        for source in module.event_inputs
    }
    scheduled = {
        source: tuple((occurrence.timestamp, payload) for occurrence, payload in occurrences)
        for source, occurrences in schedules_by_source.items()
    }
    frames = run_timestamp_kernel(
        cast(dict[object, tuple[tuple[int, EventPayload], ...]], scheduled),
        cast(dict[object, int], declaration_order),
        lambda timestamp: _dense_level_row(module, level_stream, timestamp),
        cast(dict[object, tuple[StateTransition, ...]], transitions_by_source),
        initial_state,
        lambda level_row, state, source, payload: _EventEvaluationContext(
            dict(level_row), state, cast(EventInput, source), payload
        ),
    )
    reactions = [
        EventReaction(
            timestamp=frame.timestamp,
            activations=tuple(
                EventActivation(
                    source=cast(EventInput, source),
                    payload=cast(EventPayload, payload),
                    captured_registers=tuple(
                        transition.register.name
                        for transition in frame.applied.get(source, ())
                        if transition.kind == "capture"
                    ),
                    crossing_observations=tuple(
                        SampleOnObservation(
                            crossing=crossing,
                            value=_sample_on_value(
                                crossing,
                                frame.level_row,
                                frame.state_before,
                                cast(EventInput, source),
                                cast(EventPayload, payload),
                            ),
                        )
                        for crossing in crossings_by_target[cast(EventInput, source)]
                    ),
                )
                for _, source, payload in frame.entries
            ),
            level_row=_copy_level_row(frame.level_row),
            state_before=_copy_state(frame.state_before),
            state_after=_copy_state(frame.state_after),
        )
        for frame in frames
    ]

    final_state = frames[-1].state_after if frames else initial_state
    return EventSimulationResult(
        final_state=_copy_state(final_state),
        reactions=tuple(reactions),
        domain=TimestampDomain(stop=stop_timestamp),
        event_inputs=module.event_inputs,
        sample_on_crossings=module.sample_on_crossings,
    )


def _validate_sample_on_crossings(module: CircuitModule) -> None:
    declared_scalars = set(module.inputs)
    declared_vectors = set(module.vector_inputs)
    declared_events = set(module.event_inputs)
    seen: set[SampleOn] = set()
    for crossing in module.sample_on_crossings:
        if crossing in seen:
            raise EventCrossingError("duplicate SampleOn crossing declaration")
        seen.add(crossing)
        _validate_sample_on_value(
            crossing.source,
            declared_scalars,
            declared_vectors,
            set(module.state_registers),
        )
        if crossing.target not in declared_events:
            raise EventCrossingError("SampleOn target is not a declared Event input")


def _sample_on_value(
    crossing: SampleOn,
    level_row: Mapping[str, object],
    state: StateSnapshot,
    active_source: EventInput,
    active_payload: EventPayload,
) -> EventPayload:
    if is_vector_value(crossing.source):
        return _evaluate_vector(crossing.source, level_row, state, active_source, active_payload)
    return _evaluate_scalar(crossing.source, level_row, state, active_source, active_payload)


def _validate_sample_on_value(
    value: object,
    declared_scalars: set[Input],
    declared_vectors: set[VectorInput],
    declared_registers: set[StateRegister],
    seen: set[int] | None = None,
) -> None:
    if seen is None:
        seen = set()
    if id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, (FlowInput, Input)):
        scalar_source = value.source if isinstance(value, FlowInput) else value
        if scalar_source not in declared_scalars:
            raise EventCrossingError("SampleOn source is not a declared scalar Level input")
        return
    if isinstance(value, (FlowInputSample, InputSample)):
        if value.offset != 0:
            raise EventCrossingError("SampleOn Level samples require zero logical offset")
        _validate_sample_on_value(
            value.source, declared_scalars, declared_vectors, declared_registers, seen
        )
        return
    if isinstance(value, (FlowVectorInput, VectorInput)):
        vector_source: object = value.source if isinstance(value, FlowVectorInput) else value
        if vector_source not in declared_vectors:
            raise EventCrossingError("SampleOn source is not a declared vector Level input")
        return
    if isinstance(value, (FlowVectorInputSample, VectorInputSample)):
        if value.offset != 0:
            raise EventCrossingError("SampleOn Level samples require zero logical offset")
        _validate_sample_on_value(
            value.source, declared_scalars, declared_vectors, declared_registers, seen
        )
        return
    if isinstance(value, VectorRegisterRead):
        if value.register not in declared_registers or value.offset != 0:
            raise EventCrossingError("SampleOn state reads require a declared zero-offset register")
        return
    if isinstance(value, (Constant, VectorConstant)):
        return
    if isinstance(value, VectorSignal):
        _validate_sample_on_value(
            value.vector, declared_scalars, declared_vectors, declared_registers, seen
        )
        return
    if isinstance(value, (BinaryOp, Compare)):
        _validate_sample_on_value(
            value.left, declared_scalars, declared_vectors, declared_registers, seen
        )
        _validate_sample_on_value(
            value.right, declared_scalars, declared_vectors, declared_registers, seen
        )
        return
    if isinstance(value, Select):
        for child in (value.condition, value.when_true, value.when_false):
            _validate_sample_on_value(
                child, declared_scalars, declared_vectors, declared_registers, seen
            )
        return
    if isinstance(value, VectorBinaryOp):
        _validate_sample_on_value(
            value.left, declared_scalars, declared_vectors, declared_registers, seen
        )
        _validate_sample_on_value(
            value.right, declared_scalars, declared_vectors, declared_registers, seen
        )
        return
    if isinstance(value, VectorScalarOp):
        _validate_sample_on_value(
            value.vector, declared_scalars, declared_vectors, declared_registers, seen
        )
        _validate_sample_on_value(
            value.scalar, declared_scalars, declared_vectors, declared_registers, seen
        )
        return
    if isinstance(value, (VectorFilter, VectorSelect)):
        _validate_sample_on_value(
            value.vector, declared_scalars, declared_vectors, declared_registers, seen
        )
        return
    raise EventCrossingError("SampleOn source must be a Level expression")


def materialize_event_trace(
    result: EventSimulationResult,
    reference: object,
    policy: EventMaterializationPolicy,
) -> MaterializedEventTrace:
    """Materialize one semantic Event or SampleOn reference over the simulation domain.

    This produces reference data only.  It does not add an IR stream, a bridge, or a physical
    circuit representation.
    """

    if not isinstance(policy, EventMaterializationPolicy):
        raise EventMaterializationError("materialization requires an EventMaterializationPolicy")
    candidate = _materialization_candidate(reference)
    if isinstance(candidate, EventInput):
        if candidate not in result.event_inputs:
            raise EventMaterializationError("Event reference is not declared by this simulation")
        shape = candidate.payload_shape
        updates: dict[int, EventPayload] = {}
        for reaction in result.reactions:
            for activation in reaction.activations:
                if activation.source == candidate:
                    if reaction.timestamp in updates:
                        raise EventMaterializationError(
                            "Event reference has duplicate timestamp data"
                        )
                    updates[reaction.timestamp] = _copy_payload(activation.payload)
    elif isinstance(candidate, SampleOn):
        if candidate not in result.sample_on_crossings:
            raise EventMaterializationError("SampleOn reference is not declared by this simulation")
        shape = PayloadShape.VECTOR if is_vector_value(candidate.source) else PayloadShape.SCALAR
        updates = {}
        for reaction in result.reactions:
            for activation in reaction.activations:
                for observation in activation.crossing_observations:
                    if observation.crossing == candidate:
                        if reaction.timestamp in updates:
                            raise EventMaterializationError(
                                "SampleOn reference has duplicate timestamp data"
                            )
                        updates[reaction.timestamp] = _copy_payload(observation.value)
    else:
        raise EventMaterializationError(
            "materialization reference must be a declared Event or SampleOn reference"
        )

    empty = _empty_payload(shape)
    current = _copy_payload(empty)
    rows: list[EventPayload] = []
    valid: list[bool] = []
    for timestamp in range(result.domain.start, result.domain.stop):
        present = timestamp in updates
        if present:
            current = _copy_payload(updates[timestamp])
        elif policy in (
            EventMaterializationPolicy.ZERO,
            EventMaterializationPolicy.VALID,
        ):
            current = _copy_payload(empty)
        rows.append(_copy_payload(current))
        valid.append(present)
    return MaterializedEventTrace(
        reference=reference,
        payload_shape=shape,
        policy=policy,
        domain=result.domain,
        payloads=tuple(rows),
        valid=tuple(valid) if policy is EventMaterializationPolicy.VALID else None,
    )


def _materialization_candidate(reference: object) -> EventInput | SampleOn | object:
    if isinstance(reference, (EventInput, SampleOn)):
        return reference
    try:
        candidate: object = reference.ir  # type: ignore[attr-defined]
    except AttributeError:
        return reference
    return candidate


def _empty_payload(shape: PayloadShape) -> EventPayload:
    return 0 if shape is PayloadShape.SCALAR else {}


def _copy_payload(payload: EventPayload) -> EventPayload:
    return dict(payload) if isinstance(payload, dict) else payload


def _validate_schedules(
    module: CircuitModule,
    schedules: Sequence[EventSchedule],
    clock_environment: ClockContractEnvironment,
) -> dict[EventInput, tuple[tuple[EventOccurrence, EventPayload], ...]]:
    if len(schedules) != len(module.event_inputs):
        raise EventScheduleError(
            "schedules must contain exactly one schedule for every Event source"
        )
    result: dict[EventInput, tuple[tuple[EventOccurrence, EventPayload], ...]] = {}
    for schedule in schedules:
        source = _declared_event_source(module, schedule.source)
        if source is None:
            raise EventScheduleError(f"schedule source {schedule.source.name!r} is not declared")
        if source in result:
            raise EventScheduleError(
                f"duplicate schedule for Event source {schedule.source.name!r}"
            )
        normalized: list[tuple[EventOccurrence, EventPayload]] = []
        previous: int | None = None
        for occurrence in schedule.occurrences:
            if previous is not None:
                if occurrence.timestamp <= previous:
                    raise EventScheduleError(
                        f"timestamps for Event source {source.name!r} must be strictly ordered"
                    )
                guarantee = clock_environment.contract_for(source.clock).guaranteed_min_separation
                if occurrence.timestamp - previous < guarantee:
                    raise EventScheduleError(
                        f"Event source {source.name!r} violates declared minimum separation"
                    )
            previous = occurrence.timestamp
            normalized.append((occurrence, _normalize_payload(source, occurrence.payload)))
        result[source] = tuple(normalized)
    missing = [source.name for source in module.event_inputs if source not in result]
    if missing:
        raise EventScheduleError(f"missing Event schedule(s): {', '.join(missing)}")
    return result


def _validate_event_module(
    module: CircuitModule,
    clock_environment: ClockContractEnvironment,
) -> dict[EventInput, tuple[StateTransition, ...]]:
    transitions = state_transitions(module)
    event_transitions = tuple(
        transition for transition in transitions if transition.trigger is not None
    )
    if not module.event_inputs and not event_transitions:
        if transitions:
            raise EventCausalityError("Event simulation requires declared Event inputs")
        return {}
    if any(not isinstance(operation, FreezeCapture) for operation in module.event_state_operations):
        raise EventCausalityError("unsupported Event state operation")
    transitions_by_source: dict[EventInput, list[StateTransition]] = {}
    registers: set[StateRegister] = set()
    declared_registers = set(module.state_registers)
    if any(transition.trigger is None for transition in transitions):
        raise EventCausalityError("Event modules cannot mix periodic state operations")
    for operation in event_transitions:
        if operation.register not in declared_registers:
            raise EventCausalityError("Event transition target is not a listed state register")
        if operation.trigger is None:
            raise EventCausalityError("Event transition requires an Event trigger")
        trigger = _declared_event_source(module, operation.trigger)
        if trigger is None:
            raise EventCausalityError("Event transition trigger is not declared by the module")
        if operation.kind == "capture":
            if not isinstance(operation.register, FreezeRegister):
                raise EventCausalityError("Event captures require FreezeRegister targets")
            if operation.register in registers:
                raise EventCausalityError("each FreezeReg may have only one Event capture")
            if trigger.payload_shape is PayloadShape.SCALAR and operation.value is None:
                raise EventCausalityError("scalar Event capture requires an explicit vector value")
        elif operation.kind == "add" and not isinstance(operation.register, AccumulatorRegister):
            raise EventCausalityError("Event add transitions require AccumulatorRegister targets")
        elif operation.kind == "set" and not isinstance(operation.register, FreezeRegister):
            raise EventCausalityError("Event set transitions require FreezeRegister targets")
        elif operation.kind == "clear" and not isinstance(operation.register, AccumulatorRegister):
            raise EventCausalityError("Event clear transitions require AccumulatorRegister targets")
        if operation.value is not None:
            value_clocks = _validate_event_transition_value(
                operation.value, module, PayloadShape.VECTOR, clock_environment
            )
            value_flow = getattr(operation.value, "flow", None)
            if (
                isinstance(value_flow, Flow)
                and value_flow.modality is TemporalModality.EVENT
                and value_flow.clock.clock_id != trigger.clock.clock_id
            ):
                raise EventCausalityError(
                    "Event transition value must use the transition trigger clock"
                )
            if any(clock != trigger.clock.clock_id for clock in value_clocks):
                raise EventCausalityError(
                    "Event transition value must use the transition trigger clock"
                )
        if operation.when is not None:
            when_clocks = _validate_event_transition_value(
                operation.when, module, PayloadShape.SCALAR, clock_environment
            )
            when_flow = getattr(operation.when, "flow", None)
            if (
                isinstance(when_flow, Flow)
                and when_flow.modality is TemporalModality.EVENT
                and when_flow.clock.clock_id != trigger.clock.clock_id
            ):
                raise EventCausalityError(
                    "Event transition condition must use the transition trigger clock"
                )
            if any(clock != trigger.clock.clock_id for clock in when_clocks):
                raise EventCausalityError(
                    "Event transition condition must use the transition trigger clock"
                )
        transitions_by_source.setdefault(trigger, []).append(replace(operation, trigger=trigger))
        registers.add(operation.register)
    unbound = [register.name for register in module.state_registers if register not in registers]
    if unbound:
        raise EventCausalityError(
            f"Event modules require one capture for each state register: {', '.join(unbound)}"
        )
    return {source: tuple(operations) for source, operations in transitions_by_source.items()}


def _validate_event_transition_value(
    value: object,
    module: CircuitModule,
    shape: PayloadShape,
    clock_environment: ClockContractEnvironment,
) -> set[object]:
    declared_scalars = set(module.inputs)
    declared_vectors = set(module.vector_inputs)
    declared_registers = set(module.state_registers)
    declared_events = set(module.event_inputs)
    modes: set[TemporalModality] = set()
    clocks: set[object] = set()
    seen: set[int] = set()

    def resolve_event_clock(clock: object) -> object:
        if not isinstance(clock, Clock):
            raise EventCausalityError("Event Flow is missing a valid clock")
        try:
            clock_environment.contract_for(clock)
        except KeyError as exc:
            raise EventCausalityError(
                "Event Flow clock is absent from the contract environment"
            ) from exc
        return clock.clock_id

    def visit(item: object, expected: PayloadShape) -> None:
        if id(item) in seen:
            return
        seen.add(id(item))
        if isinstance(item, EventScalarFlow):
            if expected is not PayloadShape.SCALAR or item.source not in declared_events:
                raise EventCausalityError(
                    "Event scalar Flow has an invalid transition shape/source"
                )
            modes.add(TemporalModality.EVENT)
            clocks.add(resolve_event_clock(item.flow.clock))
            return
        if isinstance(item, EventVectorFlow):
            if expected is not PayloadShape.VECTOR or item.source not in declared_events:
                raise EventCausalityError(
                    "Event vector Flow has an invalid transition shape/source"
                )
            modes.add(TemporalModality.EVENT)
            clocks.add(resolve_event_clock(item.flow.clock))
            return
        if isinstance(item, SampleOn):
            item_shape = (
                PayloadShape.VECTOR if is_vector_value(item.source) else PayloadShape.SCALAR
            )
            if item_shape is not expected or item.target not in declared_events:
                raise EventCausalityError("SampleOn has an invalid transition shape/target")
            _validate_sample_on_value(
                item.source, declared_scalars, declared_vectors, declared_registers
            )
            modes.add(TemporalModality.EVENT)
            clocks.add(resolve_event_clock(item.target.clock))
            return
        if isinstance(item, Constant):
            if expected is not PayloadShape.SCALAR:
                raise EventCausalityError("scalar constant used as a vector transition")
            return
        if isinstance(item, (Input, InputSample)):
            if expected is not PayloadShape.SCALAR:
                raise EventCausalityError("scalar Level value used as a vector transition")
            if isinstance(item, Input) and item not in declared_scalars:
                raise EventCausalityError("transition uses an undeclared scalar Level input")
            if isinstance(item, InputSample) and (
                item.source not in declared_scalars or item.offset != 0
            ):
                raise EventCausalityError("transition Level samples require zero logical offset")
            modes.add(TemporalModality.LEVEL)
            return
        if isinstance(item, VectorConstant):
            if expected is not PayloadShape.VECTOR:
                raise EventCausalityError("vector constant used as a scalar transition")
            return
        if isinstance(item, (VectorInput, VectorInputSample, VectorRegisterRead)):
            if expected is not PayloadShape.VECTOR:
                raise EventCausalityError("vector Level value used as a scalar transition")
            if isinstance(item, VectorInput) and item not in declared_vectors:
                raise EventCausalityError("transition uses an undeclared vector Level input")
            if isinstance(item, VectorInputSample) and (
                item.source not in declared_vectors or item.offset != 0
            ):
                raise EventCausalityError("transition Level samples require zero logical offset")
            if isinstance(item, VectorRegisterRead) and (
                item.register not in declared_registers or item.offset != 0
            ):
                raise EventCausalityError(
                    "transition state reads require a declared zero-offset register"
                )
            modes.add(TemporalModality.LEVEL)
            return
        if isinstance(item, VectorSignal):
            visit(item.vector, PayloadShape.VECTOR)
            return
        if isinstance(item, (BinaryOp, Compare)):
            visit(item.left, PayloadShape.SCALAR)
            visit(item.right, PayloadShape.SCALAR)
            return
        if isinstance(item, Select):
            visit(item.condition, PayloadShape.SCALAR)
            visit(item.when_true, PayloadShape.SCALAR)
            visit(item.when_false, PayloadShape.SCALAR)
            return
        if isinstance(item, VectorBinaryOp):
            visit(item.left, PayloadShape.VECTOR)
            visit(item.right, PayloadShape.VECTOR)
            return
        if isinstance(item, VectorScalarOp):
            visit(item.vector, PayloadShape.VECTOR)
            visit(item.scalar, PayloadShape.SCALAR)
            return
        if isinstance(item, (VectorFilter, VectorSelect)):
            visit(item.vector, PayloadShape.VECTOR)
            return
        raise EventCausalityError("unsupported Event transition expression")

    visit(value, shape)
    if len(modes) > 1:
        raise EventCrossingError(
            "Event and Level transition values require an explicit SampleOn conversion"
        )
    if len(clocks) > 1:
        raise EventCausalityError(
            "Event transition values must use one compatible occurrence clock"
        )
    return clocks


def _declared_event_source(module: CircuitModule, candidate: EventInput) -> EventInput | None:
    """Resolve equal structural Event values to the module-owned timing contract."""

    return next((source for source in module.event_inputs if source == candidate), None)


def _normalize_payload(source: EventInput, payload: object) -> EventPayload:
    if source.payload_shape is PayloadShape.SCALAR:
        if isinstance(payload, bool) or not isinstance(payload, int):
            raise EventScheduleError(
                f"scalar Event source {source.name!r} requires an integer payload"
            )
        return i32(payload)
    if not isinstance(payload, Mapping):
        raise EventScheduleError(
            f"vector Event source {source.name!r} requires a signal map payload"
        )
    result: SignalMap = {}
    for signal, value in payload.items():
        if not isinstance(signal, SignalId):
            raise EventScheduleError("vector Event payload keys must be SignalId values")
        if isinstance(value, bool) or not isinstance(value, int):
            raise EventScheduleError("vector Event payload values must be non-boolean integers")
        amount = i32(value)
        if amount != 0:
            result[signal] = amount
    return result


class _EventEvaluationContext(EvaluationContext):
    def __init__(
        self,
        level_row: Mapping[str, object],
        state: StateSnapshot,
        active_source: EventInput,
        event_payload: EventPayload | None = None,
    ) -> None:
        self.level_row = level_row
        self.state = state
        self.active_source = active_source
        self.event_payload = event_payload
        self.event_present = True

    def scalar_input(self, source: Input, offset: int) -> int:
        if offset != 0:
            raise EventCausalityError("Event reference values require zero logical offset")
        raw = self.level_row.get(source.name, 0)
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise EventCausalityError("Event reference scalar Level input requires an integer")
        return i32(raw)

    def vector_input(self, source: VectorInput, offset: int) -> SignalMap:
        if offset != 0:
            raise EventCausalityError("Event reference values require zero logical offset")
        raw = self.level_row.get(source.name, {})
        if not isinstance(raw, dict):
            raise EventCausalityError("Event reference vector Level input requires a signal map")
        return dict(raw)

    def state_vector(self, register: StateRegister, offset: int) -> SignalMap:
        if offset != 0:
            raise EventCausalityError("Event reference values require zero logical offset")
        return dict(self.state[register.name])

    def event_scalar(self, source: EventScalarFlow) -> int:
        if source.source != self.active_source or not self.event_present:
            raise EventCausalityError(
                f"Event source {source.name!r} is not active in this reaction"
            )
        if isinstance(self.event_payload, bool) or not isinstance(self.event_payload, int):
            raise EventCausalityError(
                f"Event scalar source {source.name!r} requires an integer payload"
            )
        return i32(self.event_payload)

    def event_vector(self, source: EventVectorFlow) -> SignalMap:
        if source.source != self.active_source or not self.event_present:
            raise EventCausalityError(
                f"Event source {source.name!r} is not active in this reaction"
            )
        if not isinstance(self.event_payload, dict):
            raise EventCausalityError(
                f"Event vector source {source.name!r} requires a signal-map payload"
            )
        return dict(self.event_payload)

    def sample_on_scalar(self, source: SampleOn) -> int:
        if source.target != self.active_source or not self.event_present:
            raise EventCausalityError("SampleOn target is not active in this reaction")
        if is_vector_value(source.source):
            raise EventCausalityError("scalar evaluator received a vector SampleOn value")
        return evaluate_scalar(cast(ScalarValue, source.source), self)

    def sample_on_vector(self, source: SampleOn) -> SignalMap:
        if source.target != self.active_source or not self.event_present:
            raise EventCausalityError("SampleOn target is not active in this reaction")
        if not is_vector_value(source.source):
            raise EventCausalityError("vector evaluator received a scalar SampleOn value")
        return evaluate_vector(cast(VectorValue, source.source), self)

    def active_event_vector(self) -> SignalMap:
        if self.active_source.payload_shape is not PayloadShape.VECTOR:
            raise EventCausalityError("a scalar Event cannot provide a vector transition payload")
        if not isinstance(self.event_payload, dict):
            raise EventCausalityError("active Event has no vector payload")
        return dict(self.event_payload)


def _evaluate_scalar(
    value: object,
    level_row: Mapping[str, object],
    state: StateSnapshot,
    active_source: EventInput,
    active_payload: EventPayload,
) -> int:
    try:
        return evaluate_scalar(
            cast(ScalarValue, value),
            _EventEvaluationContext(level_row, state, active_source, active_payload),
        )
    except TypeError as exc:
        raise EventCausalityError("unsupported Event reference scalar expression") from exc


def _evaluate_vector(
    value: object,
    level_row: Mapping[str, object],
    state: StateSnapshot,
    active_source: EventInput,
    active_payload: EventPayload,
) -> SignalMap:
    try:
        return evaluate_vector(
            cast(VectorValue, value),
            _EventEvaluationContext(level_row, state, active_source, active_payload),
        )
    except TypeError as exc:
        raise EventCausalityError("unsupported Event reference vector expression") from exc


def _dense_level_row(
    module: CircuitModule,
    level_stream: Sequence[Mapping[str, object]],
    timestamp: int,
) -> LevelRow:
    raw: Mapping[str, object] = level_stream[timestamp] if timestamp < len(level_stream) else {}
    if not isinstance(raw, Mapping):
        raise EventScheduleError("Level stream rows must be mappings")
    result: LevelRow = {}
    for scalar_source in module.inputs:
        value = raw.get(scalar_source.name, 0)
        if isinstance(value, bool) or not isinstance(value, int):
            raise EventScheduleError(
                f"scalar Level input {scalar_source.name!r} requires an integer"
            )
        result[scalar_source.name] = i32(value)
    for vector_source in module.vector_inputs:
        value = raw.get(vector_source.name, {})
        if not isinstance(value, Mapping):
            raise EventScheduleError(
                f"vector Level input {vector_source.name!r} requires a signal map"
            )
        result[vector_source.name] = _normalize_level_map(value)
    return result


def _normalize_level_map(value: Mapping[object, object]) -> SignalMap:
    result: SignalMap = {}
    for signal, amount in value.items():
        if not isinstance(signal, SignalId):
            raise EventScheduleError("Level vector payload keys must be SignalId values")
        if isinstance(amount, bool) or not isinstance(amount, int):
            raise EventScheduleError("Level vector payload values must be non-boolean integers")
        normalized = i32(amount)
        if normalized != 0:
            result[signal] = normalized
    return result


def _copy_state(state: StateSnapshot) -> StateSnapshot:
    return {name: dict(value) for name, value in state.items()}


def _copy_level_row(row: Mapping[str, object]) -> LevelRow:
    return {name: dict(value) if isinstance(value, dict) else value for name, value in row.items()}


__all__ = [
    "EventActivation",
    "EventCausalityError",
    "EventCrossingError",
    "EventMaterializationError",
    "EventMaterializationPolicy",
    "EventOccurrence",
    "EventReaction",
    "EventSchedule",
    "EventScheduleError",
    "EventSimulationResult",
    "EventThroughputError",
    "MaterializedEventTrace",
    "SampleOnObservation",
    "TimestampDomain",
    "materialize_event_trace",
    "simulate_events",
]
