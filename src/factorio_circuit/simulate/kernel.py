"""Shared semantic expression evaluation for Level and reference-only Event adapters.

The kernel knows the value algebra, not a timestamp source or a state-storage policy.  Adapters
provide current Level samples, state observations, and (for the Event lane) present Event payloads.
This keeps Event simulation reference-only while preventing its evaluator from drifting away from
the ordinary semantic evaluator.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Protocol, cast

from factorio_circuit.ir.physical import SignalId
from factorio_circuit.ir.semantic import (
    BinaryOp,
    Compare,
    Constant,
    EventScalarFlow,
    EventVectorFlow,
    Input,
    InputSample,
    SampleOn,
    ScalarValue,
    Select,
    VectorBinaryOp,
    VectorConstant,
    VectorFilter,
    VectorInput,
    VectorInputSample,
    VectorScalarOp,
    VectorSelect,
    VectorSignal,
    VectorValue,
)
from factorio_circuit.ir.state import StateRegister, StateTransition, VectorRegisterRead
from factorio_circuit.target.factorio.semantics import apply_binary, apply_compare, i32

type SignalMap = dict[SignalId, int]
type EventPayload = int | SignalMap
type StateSnapshot = dict[str, SignalMap]


class EvaluationContext(Protocol):
    """Environment callbacks required by the shared semantic evaluator."""

    def scalar_input(self, source: Input, offset: int) -> int: ...

    def vector_input(self, source: VectorInput, offset: int) -> SignalMap: ...

    def state_vector(self, register: StateRegister, offset: int) -> SignalMap: ...

    def event_scalar(self, source: EventScalarFlow) -> int: ...

    def event_vector(self, source: EventVectorFlow) -> SignalMap: ...

    def sample_on_scalar(self, source: SampleOn) -> int: ...

    def sample_on_vector(self, source: SampleOn) -> SignalMap: ...

    def active_event_vector(self) -> SignalMap: ...


def evaluate_scalar(value: ScalarValue, context: EvaluationContext) -> int:
    if isinstance(value, EventScalarFlow):
        return context.event_scalar(value)
    if isinstance(value, SampleOn):
        return context.sample_on_scalar(value)
    if isinstance(value, Input):
        return context.scalar_input(value, 0)
    if isinstance(value, InputSample):
        return context.scalar_input(value.source, value.offset)
    if isinstance(value, Constant):
        return i32(value.value)
    if isinstance(value, VectorSignal):
        vector = evaluate_vector(value.vector, context)
        if value.signal == SignalId("virtual", "signal-anything"):
            return int(bool(vector))
        return vector.get(value.signal, 0)
    if isinstance(value, BinaryOp):
        return apply_binary(
            value.op,
            evaluate_scalar(value.left, context),
            evaluate_scalar(value.right, context),
        )
    if isinstance(value, Compare):
        return int(
            apply_compare(
                value.op,
                evaluate_scalar(value.left, context),
                evaluate_scalar(value.right, context),
            )
        )
    if isinstance(value, Select):
        branch = (
            value.when_true if evaluate_scalar(value.condition, context) != 0 else value.when_false
        )
        return evaluate_scalar(branch, context)
    raise TypeError(value)


def evaluate_vector(value: VectorValue, context: EvaluationContext) -> SignalMap:
    if isinstance(value, EventVectorFlow):
        return context.event_vector(value)
    if isinstance(value, SampleOn):
        return context.sample_on_vector(value)
    if isinstance(value, VectorInput):
        return context.vector_input(value, 0)
    if isinstance(value, VectorInputSample):
        return context.vector_input(value.source, value.offset)
    if isinstance(value, VectorRegisterRead):
        return context.state_vector(value.register, value.offset)
    if isinstance(value, VectorConstant):
        return {signal: i32(amount) for signal, amount in value.signals if i32(amount) != 0}
    if isinstance(value, VectorBinaryOp):
        left = evaluate_vector(value.left, context)
        right = evaluate_vector(value.right, context)
        return _vector_binary(value.op, left, right)
    if isinstance(value, VectorScalarOp):
        vector = evaluate_vector(value.vector, context)
        scalar = evaluate_scalar(value.scalar, context)
        return {
            signal: i32(apply_binary(value.op, amount, scalar))
            for signal, amount in vector.items()
            if i32(apply_binary(value.op, amount, scalar)) != 0
        }
    if isinstance(value, VectorSelect):
        vector = evaluate_vector(value.vector, context)
        if not vector:
            return {}
        if value.select_max:
            signal, amount = max(
                vector.items(), key=lambda item: (item[1], item[0].kind, item[0].name)
            )
            return {signal: amount}
        return dict(vector)
    if isinstance(value, VectorFilter):
        vector = evaluate_vector(value.vector, context)
        return {
            signal: amount
            for signal, amount in vector.items()
            if apply_compare(value.op, amount, value.right)
        }
    raise TypeError(value)


def _vector_binary(op: str, left: SignalMap, right: SignalMap) -> SignalMap:
    result: SignalMap = {}
    for signal in set(left) | set(right):
        amount = i32(apply_binary(op, left.get(signal, 0), right.get(signal, 0)))
        if amount != 0:
            result[signal] = amount
    return result


def apply_state_transition(
    current: SignalMap,
    transition: StateTransition,
    context: EvaluationContext,
) -> SignalMap:
    """Apply one canonical transition using the context's pre-state observations."""

    if transition.kind == "capture":
        value = (
            context.active_event_vector()
            if transition.value is None
            else evaluate_vector(transition.value, context)
        )
        return dict(value)
    if transition.when is not None and evaluate_scalar(transition.when, context) == 0:
        return dict(current)
    if transition.kind == "clear":
        return {}
    if transition.kind == "set":
        if transition.value is None:
            raise ValueError("set transition requires a vector value")
        return evaluate_vector(transition.value, context)
    if transition.kind == "add":
        if transition.value is None:
            raise ValueError("add transition requires a vector value")
        result = dict(current)
        for signal, amount in evaluate_vector(transition.value, context).items():
            updated = i32(result.get(signal, 0) + amount)
            if updated == 0:
                result.pop(signal, None)
            else:
                result[signal] = updated
        return result
    raise ValueError(f"unsupported state transition kind {transition.kind!r}")


def apply_state_transitions(
    current: SignalMap,
    transitions: Sequence[StateTransition],
    context: EvaluationContext,
) -> SignalMap:
    """Apply one register's transitions while preserving legacy clear-before-add behavior."""

    ordered = sorted(transitions, key=lambda transition: transition.order)
    for transition in ordered:
        if transition.kind == "clear" and (
            transition.when is None or evaluate_scalar(transition.when, context) != 0
        ):
            return {}
    result = dict(current)
    for transition in ordered:
        if transition.kind == "clear":
            continue
        result = apply_state_transition(result, transition, context)
    return result


@dataclass(frozen=True, slots=True)
class TimestampReaction:
    """Atomic timestamp result shared by the Event reference adapter."""

    timestamp: int
    entries: tuple[tuple[int, object, object], ...]
    level_row: Mapping[str, object]
    state_before: StateSnapshot
    state_after: StateSnapshot
    applied: Mapping[object, tuple[StateTransition, ...]]


type ReactionBatch = tuple[int, Sequence[tuple[int, object, object]]]


def run_reaction_kernel(
    batches: Iterable[ReactionBatch],
    level_snapshot: Callable[[int], Mapping[str, object]],
    initial_state: StateSnapshot,
    transition_resolver: Callable[[object, object], Sequence[StateTransition]],
    context_factory: Callable[
        [Mapping[str, object], StateSnapshot, object, object], EvaluationContext
    ],
    after_frame: Callable[[TimestampReaction], None] | None = None,
) -> tuple[TimestampReaction, ...]:
    """Execute timestamp/boundary batches with one snapshot and atomic state commit."""
    state = {name: dict(value) for name, value in initial_state.items()}
    reactions: list[TimestampReaction] = []
    for timestamp, raw_entries in batches:
        level_row = level_snapshot(timestamp)
        before = {name: dict(value) for name, value in state.items()}
        staged = {name: dict(value) for name, value in state.items()}
        entries = tuple(sorted(raw_entries, key=lambda item: item[0]))
        applied: dict[object, tuple[StateTransition, ...]] = {}
        for _, source, payload in entries:
            source_transitions = tuple(transition_resolver(source, payload))
            applied[source] = source_transitions
            by_register: dict[str, list[StateTransition]] = {}
            for transition in source_transitions:
                by_register.setdefault(transition.register.name, []).append(transition)
            context = context_factory(level_row, before, source, payload)
            for register_name, register_transitions in by_register.items():
                staged[register_name] = apply_state_transitions(
                    staged.get(register_name, {}), register_transitions, context
                )
        state = staged
        reactions.append(
            TimestampReaction(
                timestamp=timestamp,
                entries=entries,
                level_row=dict(level_row),
                state_before=before,
                state_after={name: dict(value) for name, value in state.items()},
                applied=applied,
            )
        )
        if after_frame is not None:
            after_frame(reactions[-1])
    return tuple(reactions)


@dataclass(frozen=True, slots=True)
class _IndexedEventPayload:
    occurrence_index: int
    payload: EventPayload


def run_timestamp_kernel(
    scheduled: Mapping[object, Sequence[tuple[int, EventPayload]]],
    declaration_order: Mapping[object, int],
    level_snapshot: Callable[[int], Mapping[str, object]],
    transitions_by_source: Mapping[object, Sequence[StateTransition]],
    initial_state: StateSnapshot,
    context_factory: Callable[
        [Mapping[str, object], StateSnapshot, object, EventPayload], EvaluationContext
    ],
) -> tuple[TimestampReaction, ...]:
    """Event adapter preserving the historical schedule-oriented kernel API.

    ``StateTransition.logical_offset`` is measured in source occurrences.  A transition at offset
    ``n`` is dormant for the first ``n`` occurrences and then executes on the reindexed tail.  The
    expression evaluator still sees the payload of the active physical occurrence, which is exactly
    the payload selected by a flow-local Event ``step(n)`` boundary.
    """

    grouped: dict[int, list[tuple[int, object, object]]] = {}
    for source, occurrences in scheduled.items():
        source_order = declaration_order[source]
        for occurrence_index, (timestamp, payload) in enumerate(occurrences):
            grouped.setdefault(timestamp, []).append(
                (source_order, source, _IndexedEventPayload(occurrence_index, payload))
            )

    frames = run_reaction_kernel(
        ((timestamp, tuple(entries)) for timestamp, entries in sorted(grouped.items())),
        level_snapshot,
        initial_state,
        lambda source, indexed: tuple(
            transition
            for transition in transitions_by_source.get(source, ())
            if transition.logical_offset
            <= cast(_IndexedEventPayload, indexed).occurrence_index
        ),
        lambda level_row, state, source, indexed: context_factory(
            level_row,
            state,
            source,
            cast(_IndexedEventPayload, indexed).payload,
        ),
    )
    return tuple(
        replace(
            frame,
            entries=tuple(
                (
                    order,
                    source,
                    cast(_IndexedEventPayload, indexed).payload,
                )
                for order, source, indexed in frame.entries
            ),
        )
        for frame in frames
    )