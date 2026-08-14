"""Semantic/reference simulation for the Phase 3 Event vertical slice.

This path is intentionally separate from the ordinary Level stream simulator.  It models only
deterministic Event occurrences, same-timestamp snapshots, and FreezeReg captures; it does not
model physical pulses, buffering, output streams, or bridges.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

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
    Compare,
    Constant,
    EventInput,
    Input,
    InputSample,
    PayloadShape,
    SampleOn,
    Select,
    VectorConstant,
    VectorInput,
    VectorInputSample,
    VectorSignal,
)
from factorio_circuit.ir.state import (
    FreezeCapture,
    FreezeRegister,
    StateRegister,
    VectorRegisterRead,
)
from factorio_circuit.target.factorio.semantics import apply_binary, apply_compare, i32

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

    _validate_sample_on_crossings(module)
    schedules_by_source = _validate_schedules(module, schedules)
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
    captures = _validate_event_module(module)
    state: StateSnapshot = {register.name: {} for register in module.state_registers}
    grouped: dict[int, list[tuple[int, EventInput, EventOccurrence, EventPayload]]] = {}
    declaration_order = {source: index for index, source in enumerate(module.event_inputs)}
    crossings_by_target: dict[EventInput, tuple[SampleOn, ...]] = {
        source: tuple(
            crossing for crossing in module.sample_on_crossings if crossing.target == source
        )
        for source in module.event_inputs
    }
    for source, occurrences in schedules_by_source.items():
        source_order = declaration_order[source]
        for occurrence, payload in occurrences:
            grouped.setdefault(occurrence.timestamp, []).append(
                (source_order, source, occurrence, payload)
            )

    reactions: list[EventReaction] = []
    for timestamp in sorted(grouped):
        level_row = _dense_level_row(module, level_stream, timestamp)
        before = _copy_state(state)
        staged = _copy_state(state)
        entries = sorted(grouped[timestamp], key=lambda item: item[0])
        captured_by_source: dict[EventInput, list[str]] = {}
        for _, source, _occurrence, payload in entries:
            for capture in captures.get(source, ()):
                captured_by_source.setdefault(source, []).append(capture.register.name)
                captured = (
                    payload
                    if capture.value is None
                    else _evaluate_capture_value(capture.value, level_row, before)
                )
                if not isinstance(captured, dict):
                    raise EventCausalityError("vector Event capture produced a non-vector payload")
                staged[capture.register.name] = dict(captured)

        after = _copy_state(staged)
        reactions.append(
            EventReaction(
                timestamp=timestamp,
                activations=tuple(
                    EventActivation(
                        source=activation_source,
                        payload=activation_payload,
                        captured_registers=tuple(captured_by_source.get(activation_source, ())),
                        crossing_observations=tuple(
                            SampleOnObservation(
                                crossing=crossing,
                                value=_sample_on_value(crossing, level_row),
                            )
                            for crossing in crossings_by_target[activation_source]
                        ),
                    )
                    for _, activation_source, _, activation_payload in entries
                ),
                level_row=_copy_level_row(level_row),
                state_before=_copy_state(before),
                state_after=_copy_state(after),
            )
        )
        state = staged

    return EventSimulationResult(
        final_state=_copy_state(state),
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
        if isinstance(crossing.source, Input):
            if crossing.source not in declared_scalars:
                raise EventCrossingError("SampleOn source is not a declared scalar Level input")
        elif isinstance(crossing.source, VectorInput):
            if crossing.source not in declared_vectors:
                raise EventCrossingError("SampleOn source is not a declared vector Level input")
        else:
            raise EventCrossingError("SampleOn source must be a raw Level input")
        if crossing.target not in declared_events:
            raise EventCrossingError("SampleOn target is not a declared Event input")


def _sample_on_value(crossing: SampleOn, level_row: LevelRow) -> EventPayload:
    raw = level_row.get(crossing.source.name, {} if isinstance(crossing.source, VectorInput) else 0)
    if isinstance(crossing.source, Input):
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise EventCausalityError("SampleOn scalar Level input requires an integer")
        return i32(raw)
    if not isinstance(raw, dict):
        raise EventCausalityError("SampleOn vector Level input requires a signal map")
    return dict(raw)


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
        shape = PayloadShape.SCALAR if isinstance(candidate.source, Input) else PayloadShape.VECTOR
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
) -> dict[EventInput, tuple[tuple[EventOccurrence, EventPayload], ...]]:
    declared = set(module.event_inputs)
    if len(schedules) != len(module.event_inputs):
        raise EventScheduleError(
            "schedules must contain exactly one schedule for every Event source"
        )
    result: dict[EventInput, tuple[tuple[EventOccurrence, EventPayload], ...]] = {}
    for schedule in schedules:
        if schedule.source not in declared:
            raise EventScheduleError(f"schedule source {schedule.source.name!r} is not declared")
        if schedule.source in result:
            raise EventScheduleError(
                f"duplicate schedule for Event source {schedule.source.name!r}"
            )
        normalized: list[tuple[EventOccurrence, EventPayload]] = []
        previous: int | None = None
        for occurrence in schedule.occurrences:
            if previous is not None:
                if occurrence.timestamp <= previous:
                    raise EventScheduleError(
                        f"timestamps for Event source {schedule.source.name!r} must be strictly "
                        "ordered"
                    )
                if (
                    occurrence.timestamp - previous
                    < schedule.source.clock.guaranteed_min_separation
                ):
                    raise EventScheduleError(
                        f"Event source {schedule.source.name!r} violates declared minimum "
                        "separation"
                    )
            previous = occurrence.timestamp
            normalized.append((occurrence, _normalize_payload(schedule.source, occurrence.payload)))
        result[schedule.source] = tuple(normalized)
    missing = [source.name for source in module.event_inputs if source not in result]
    if missing:
        raise EventScheduleError(f"missing Event schedule(s): {', '.join(missing)}")
    return result


def _validate_event_module(module: CircuitModule) -> dict[EventInput, tuple[FreezeCapture, ...]]:
    if not module.event_inputs and not module.event_state_operations:
        if module.state_operations:
            raise EventCausalityError("Event simulation requires declared Event inputs")
        return {}
    captures: dict[EventInput, list[FreezeCapture]] = {}
    registers: set[StateRegister] = set()
    declared = set(module.event_inputs)
    declared_registers = set(module.state_registers)
    if module.state_operations:
        raise EventCausalityError("Event modules cannot mix periodic state operations")
    for operation in module.event_state_operations:
        if not isinstance(operation, FreezeCapture):
            raise EventCausalityError("unsupported Event state operation")
        if not isinstance(operation.register, FreezeRegister):
            raise EventCausalityError("Event captures require FreezeRegister targets")
        if operation.register not in declared_registers:
            raise EventCausalityError("Event capture target is not a listed state register")
        if operation.trigger not in declared:
            raise EventCausalityError("Event capture trigger is not declared by the module")
        if operation.register in registers:
            raise EventCausalityError("each FreezeReg may have only one Event capture")
        if operation.trigger.payload_shape.value == "scalar" and operation.value is None:
            raise EventCausalityError("scalar Event capture requires an explicit vector value")
        if operation.value is not None:
            _validate_capture_value(operation.value)
        if operation.trigger.clock.guaranteed_min_separation < operation.required_min_separation:
            raise EventThroughputError(
                f"Event source {operation.trigger.name!r} guarantee is below capture requirement"
            )
        captures.setdefault(operation.trigger, []).append(operation)
        registers.add(operation.register)
    unbound = [register.name for register in module.state_registers if register not in registers]
    if unbound:
        raise EventCausalityError(
            f"Event modules require one capture for each state register: {', '.join(unbound)}"
        )
    return {source: tuple(operations) for source, operations in captures.items()}


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


def _validate_capture_value(value: object) -> None:
    from factorio_circuit.frontend import (
        _VectorBinaryOp,
        _VectorFilter,
        _VectorScalarOp,
        _VectorSelect,
    )

    def validate_scalar(item: object) -> None:
        if isinstance(item, InputSample):
            if item.offset != 0:
                raise EventCausalityError(
                    "Event capture values require zero-offset Level/state inputs"
                )
            return
        if isinstance(item, (Input, Constant)):
            return
        if isinstance(item, VectorSignal):
            validate_vector(item.vector)
            return
        if isinstance(item, (BinaryOp, Compare)):
            validate_scalar(item.left)
            validate_scalar(item.right)
            return
        if isinstance(item, Select):
            validate_scalar(item.condition)
            validate_scalar(item.when_true)
            validate_scalar(item.when_false)
            return
        raise EventCausalityError("unsupported Event capture scalar expression")

    def validate_vector(item: object) -> None:
        if isinstance(item, VectorInput):
            return
        if isinstance(item, VectorInputSample):
            if item.offset != 0:
                raise EventCausalityError(
                    "Event capture values require zero-offset Level/state inputs"
                )
            return
        if isinstance(item, VectorRegisterRead):
            if item.offset != 0:
                raise EventCausalityError(
                    "Event capture values require zero-offset Level/state inputs"
                )
            return
        if isinstance(item, VectorConstant):
            return
        if isinstance(item, _VectorBinaryOp):
            validate_vector(item.left)
            validate_vector(item.right)
            return
        if isinstance(item, _VectorScalarOp):
            validate_vector(item.vector)
            validate_scalar(item.scalar)
            return
        if isinstance(item, (_VectorSelect, _VectorFilter)):
            validate_vector(item.vector)
            return
        raise EventCausalityError("unsupported Event capture vector expression")

    validate_vector(value)


def _evaluate_capture_value(
    value: object,
    level_row: LevelRow,
    state: StateSnapshot,
) -> SignalMap:
    _validate_capture_value(value)
    return _evaluate_vector(value, level_row, state)


def _evaluate_scalar(value: object, level_row: LevelRow, state: StateSnapshot) -> int:
    if isinstance(value, Input):
        raw = level_row.get(value.name, 0)
        if not isinstance(raw, int):
            raise EventCausalityError("Event capture scalar Level input requires an integer")
        return i32(raw)
    if isinstance(value, InputSample):
        if value.offset != 0:
            raise EventCausalityError("Event capture values require zero-offset Level/state inputs")
        return _evaluate_scalar(value.source, level_row, state)
    if isinstance(value, Constant):
        return i32(value.value)
    if isinstance(value, VectorSignal):
        vector = _evaluate_vector(value.vector, level_row, state)
        if value.signal == ANYTHING_SIGNAL:
            return int(bool(vector))
        return vector.get(value.signal, 0)
    if isinstance(value, BinaryOp):
        return apply_binary(
            value.op,
            _evaluate_scalar(value.left, level_row, state),
            _evaluate_scalar(value.right, level_row, state),
        )
    if isinstance(value, Compare):
        return int(
            apply_compare(
                value.op,
                _evaluate_scalar(value.left, level_row, state),
                _evaluate_scalar(value.right, level_row, state),
            )
        )
    if isinstance(value, Select):
        branch = (
            value.when_true
            if _evaluate_scalar(value.condition, level_row, state) != 0
            else value.when_false
        )
        return _evaluate_scalar(branch, level_row, state)
    raise EventCausalityError("unsupported Event capture scalar expression")


def _evaluate_vector(value: object, level_row: LevelRow, state: StateSnapshot) -> SignalMap:
    from factorio_circuit.frontend import (
        _VectorBinaryOp,
        _VectorFilter,
        _VectorScalarOp,
        _VectorSelect,
    )

    if isinstance(value, VectorInput):
        raw = level_row.get(value.name, {})
        if not isinstance(raw, dict):
            raise EventCausalityError("Event capture Level value requires a signal map")
        return dict(raw)
    if isinstance(value, VectorInputSample):
        if value.offset != 0:
            raise EventCausalityError("Event capture values require zero-offset Level/state inputs")
        raw = level_row.get(value.source.name, {})
        if not isinstance(raw, dict):
            raise EventCausalityError("Event capture Level value requires a signal map")
        return dict(raw)
    if isinstance(value, VectorRegisterRead):
        if value.offset != 0:
            raise EventCausalityError("Event capture values require zero-offset Level/state inputs")
        return dict(state[value.register.name])
    if isinstance(value, VectorConstant):
        return {signal: i32(amount) for signal, amount in value.signals if i32(amount) != 0}
    if isinstance(value, _VectorBinaryOp):
        left = _evaluate_vector(value.left, level_row, state)
        right = _evaluate_vector(value.right, level_row, state)
        return _vector_binary(value.op, left, right)
    if isinstance(value, _VectorScalarOp):
        vector = _evaluate_vector(value.vector, level_row, state)
        scalar = _evaluate_scalar(value.scalar, level_row, state)
        return {
            signal: i32(apply_binary(value.op, amount, scalar))
            for signal, amount in vector.items()
            if i32(apply_binary(value.op, amount, scalar)) != 0
        }
    if isinstance(value, _VectorSelect):
        vector = _evaluate_vector(value.vector, level_row, state)
        if not vector:
            return {}
        if value.select_max:
            signal, amount = max(
                vector.items(), key=lambda item: (item[1], item[0].kind, item[0].name)
            )
            return {signal: amount}
        return dict(vector)
    if isinstance(value, _VectorFilter):
        vector = _evaluate_vector(value.vector, level_row, state)
        return {
            signal: amount
            for signal, amount in vector.items()
            if apply_compare(value.op, amount, value.right)
        }
    raise EventCausalityError("unsupported Event capture vector expression")


def _vector_binary(op: str, left: SignalMap, right: SignalMap) -> SignalMap:
    signals = set(left) | set(right)
    result: SignalMap = {}
    for signal in signals:
        amount = i32(apply_binary(op, left.get(signal, 0), right.get(signal, 0)))
        if amount != 0:
            result[signal] = amount
    return result


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


def _copy_level_row(row: LevelRow) -> LevelRow:
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
