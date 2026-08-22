"""Implementation-neutral problem records for temporal technology mapping.

This layer contains semantic data dependencies plus target-independent occurrence information. It
deliberately has no ordinary Factorio combinator latency. Fixed external availability belongs to
``MappingSource``; unresolved state read/write port timing belongs to explicit state records and is
chosen only after a physical state-cell implementation exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from factorio_circuit.ir.semantic import PayloadShape
from factorio_circuit.ir.state import StateTransition, VectorRegisterRead


class MappingProblemError(ValueError):
    """Raised when an implementation-neutral mapping problem is malformed."""


class MappingSourceMode(StrEnum):
    """Physical observation contract supplied for one fixed semantic leaf."""

    STABLE = "stable"
    OBSERVABLE = "observable"
    EXACT = "exact"


@dataclass(frozen=True, slots=True)
class MappingSource:
    id: int
    label: str
    shape: PayloadShape
    mode: MappingSourceMode
    semantic: object
    start_phase: int = 0
    end_phase_exclusive: int | None = None

    def __post_init__(self) -> None:
        _require_positive_id(self.id, "source")
        if not self.label:
            raise MappingProblemError("mapping source label must be non-empty")
        if not isinstance(self.shape, PayloadShape):
            raise MappingProblemError("mapping source shape must be a PayloadShape")
        if not isinstance(self.mode, MappingSourceMode):
            raise MappingProblemError("mapping source mode must be a MappingSourceMode")
        _require_phase(self.start_phase, "mapping source start phase")
        if self.end_phase_exclusive is not None:
            _require_phase(self.end_phase_exclusive, "mapping source end phase")
            if self.end_phase_exclusive <= self.start_phase:
                raise MappingProblemError("mapping source availability interval must be non-empty")
        if self.mode is MappingSourceMode.EXACT and self.end_phase_exclusive not in {
            None,
            self.start_phase + 1,
        }:
            raise MappingProblemError("EXACT sources denote one chosen physical tick")

    @property
    def last_free_phase(self) -> int | None:
        if self.mode is MappingSourceMode.EXACT:
            return self.start_phase
        if self.end_phase_exclusive is None:
            return None
        return self.end_phase_exclusive - 1


@dataclass(frozen=True, slots=True)
class MappingStateRead:
    """One semantic register occurrence before a physical state-cell implementation is chosen.

    Unlike ``MappingSource``, this record has no physical availability interval. A state-cell
    candidate exports the read port's phase/availability contract. ``logical_offset`` remains the
    implementation-independent occurrence displacement from canonical semantic IR.
    """

    id: int
    label: str
    semantic: VectorRegisterRead

    def __post_init__(self) -> None:
        _require_positive_id(self.id, "state read")
        if not self.label:
            raise MappingProblemError("mapping state read label must be non-empty")
        if not isinstance(self.semantic, VectorRegisterRead):
            raise MappingProblemError("mapping state read requires a VectorRegisterRead semantic")

    @property
    def shape(self) -> PayloadShape:
        return PayloadShape.VECTOR

    @property
    def register_name(self) -> str:
        return self.semantic.register.name

    @property
    def logical_offset(self) -> int:
        return self.semantic.offset


@dataclass(frozen=True, slots=True)
class MappingOperation:
    """One semantic recipe whose implementation has not been chosen yet."""

    id: int
    label: str
    shape: PayloadShape
    operands: tuple[int, ...]
    semantic: object

    def __post_init__(self) -> None:
        _require_positive_id(self.id, "operation")
        if not self.label:
            raise MappingProblemError("mapping operation label must be non-empty")
        if not isinstance(self.shape, PayloadShape):
            raise MappingProblemError("mapping operation shape must be a PayloadShape")
        if not self.operands:
            raise MappingProblemError("mapping operation must have at least one operand")
        if any(
            not isinstance(item, int) or isinstance(item, bool) or item <= 0
            for item in self.operands
        ):
            raise MappingProblemError("mapping operation operands must be positive value ids")


@dataclass(frozen=True, slots=True)
class MappingSink:
    """One implementation-independent demand for a semantic value at a fixed phase."""

    id: int
    label: str
    value: int
    phase: int

    def __post_init__(self) -> None:
        _require_positive_id(self.id, "sink")
        if not self.label:
            raise MappingProblemError("mapping sink label must be non-empty")
        _require_positive_id(self.value, "sink value")
        _require_phase(self.phase, "mapping sink phase")


@dataclass(frozen=True, slots=True)
class MappingStateTransition:
    """One periodic semantic state-update obligation before a state-cell implementation is chosen.

    ``value`` and ``when`` are mapping value ids for the canonical transition's data/control
    expressions. No physical consume phase is stored here. A state-cell implementation candidate
    owns those port timing equations instead of importing ``transition_input_phase`` from the
    established state-timing analyzer.
    """

    id: int
    label: str
    value: int | None
    when: int | None
    semantic: StateTransition

    def __post_init__(self) -> None:
        _require_positive_id(self.id, "state transition")
        if not self.label:
            raise MappingProblemError("mapping state transition label must be non-empty")
        if not isinstance(self.semantic, StateTransition):
            raise MappingProblemError("mapping state transition requires a StateTransition semantic")
        if self.semantic.trigger is not None:
            raise MappingProblemError("periodic mapping state transition cannot be Event-triggered")
        if self.value is not None:
            _require_positive_id(self.value, "state transition value")
        if self.when is not None:
            _require_positive_id(self.when, "state transition condition")
        if (self.value is None) != (self.semantic.value is None):
            raise MappingProblemError(
                "mapping state transition value presence disagrees with semantic transition"
            )
        if (self.when is None) != (self.semantic.when is None):
            raise MappingProblemError(
                "mapping state transition condition presence disagrees with semantic transition"
            )

    @property
    def register_name(self) -> str:
        return self.semantic.register.name

    @property
    def kind(self) -> str:
        return self.semantic.kind

    @property
    def logical_offset(self) -> int:
        return self.semantic.logical_offset


@dataclass(frozen=True, slots=True, order=True)
class MappingUse:
    """One producer use before any physical delivery mechanism is chosen."""

    producer: int
    consumer: int
    operand_index: int | None


@dataclass(frozen=True, slots=True)
class MappingProblem:
    """Semantic dependency problem handed to the joint temporal technology mapper."""

    horizon: int
    sources: tuple[MappingSource, ...]
    operations: tuple[MappingOperation, ...]
    sinks: tuple[MappingSink, ...]
    state_reads: tuple[MappingStateRead, ...] = ()
    state_transitions: tuple[MappingStateTransition, ...] = ()
    period: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.horizon, bool) or not isinstance(self.horizon, int) or self.horizon < 0:
            raise MappingProblemError("mapping horizon must be a non-negative integer")
        if self.period is not None and (
            isinstance(self.period, bool) or not isinstance(self.period, int) or self.period < 1
        ):
            raise MappingProblemError("mapping period must be a positive integer when prescribed")
        if (self.state_reads or self.state_transitions) and self.period is None:
            raise MappingProblemError("stateful mapping problems require a prescribed logical period")
        self.validate()

    @property
    def value_ids(self) -> frozenset[int]:
        return frozenset(
            {source.id for source in self.sources}
            | {read.id for read in self.state_reads}
            | {operation.id for operation in self.operations}
        )

    def source_by_id(self, value_id: int) -> MappingSource:
        for source in self.sources:
            if source.id == value_id:
                return source
        raise KeyError(value_id)

    def state_read_by_id(self, value_id: int) -> MappingStateRead:
        for read in self.state_reads:
            if read.id == value_id:
                return read
        raise KeyError(value_id)

    def operation_by_id(self, value_id: int) -> MappingOperation:
        for operation in self.operations:
            if operation.id == value_id:
                return operation
        raise KeyError(value_id)

    def state_transition_by_id(self, transition_id: int) -> MappingStateTransition:
        for transition in self.state_transitions:
            if transition.id == transition_id:
                return transition
        raise KeyError(transition_id)

    def uses(self) -> tuple[MappingUse, ...]:
        """Return physical-phase uses supported by the stateless mapping solver.

        The first stateful solver has its own candidate-owned state-port use construction. Keep this
        method strict so legacy/stateless callers cannot silently invent a state-cell ABI.
        """

        if self.state_reads or self.state_transitions:
            raise MappingProblemError(
                "stateful temporal mapping requires the periodic state solver so state-cell "
                "candidates can own read/write use phases"
            )
        result = [
            MappingUse(producer, operation.id, operand_index)
            for operation in self.operations
            for operand_index, producer in enumerate(operation.operands)
        ]
        result.extend(MappingUse(sink.value, sink.id, None) for sink in self.sinks)
        return tuple(result)

    def validate(self) -> None:
        value_ids = (
            [item.id for item in self.sources]
            + [item.id for item in self.state_reads]
            + [item.id for item in self.operations]
        )
        sink_ids = [item.id for item in self.sinks]
        transition_ids = [item.id for item in self.state_transitions]
        if len(set(value_ids)) != len(value_ids):
            raise MappingProblemError("mapping value ids must be unique")
        if len(set(sink_ids)) != len(sink_ids):
            raise MappingProblemError("mapping sink ids must be unique")
        if len(set(transition_ids)) != len(transition_ids):
            raise MappingProblemError("mapping state transition ids must be unique")
        namespaces = (set(value_ids), set(sink_ids), set(transition_ids))
        overlap = any(
            namespaces[left] & namespaces[right]
            for left in range(len(namespaces))
            for right in range(left + 1, len(namespaces))
        )
        if overlap:
            raise MappingProblemError(
                "mapping values, sinks, and state transitions must use disjoint namespaces"
            )

        known = set(value_ids)
        operations = {item.id: item for item in self.operations}
        for operation in self.operations:
            missing = [item for item in operation.operands if item not in known]
            if missing:
                raise MappingProblemError(
                    f"operation {operation.label!r} references unknown value ids {missing}"
                )
            if operation.id in operation.operands:
                raise MappingProblemError("mapping operation cannot directly depend on itself")
        for sink in self.sinks:
            if sink.value not in known:
                raise MappingProblemError(
                    f"sink {sink.label!r} references unknown value id {sink.value}"
                )
            if sink.phase > self.horizon:
                raise MappingProblemError(
                    f"sink {sink.label!r} phase {sink.phase} exceeds horizon {self.horizon}"
                )
        for transition in self.state_transitions:
            references = tuple(
                item for item in (transition.value, transition.when) if item is not None
            )
            missing = [item for item in references if item not in known]
            if missing:
                raise MappingProblemError(
                    f"state transition {transition.label!r} references unknown value ids {missing}"
                )
        for source in self.sources:
            if source.start_phase > self.horizon:
                raise MappingProblemError(
                    f"source {source.label!r} starts after mapping horizon {self.horizon}"
                )

        visiting: set[int] = set()
        visited: set[int] = set()

        def visit(value_id: int) -> None:
            if value_id in visited or value_id not in operations:
                return
            if value_id in visiting:
                raise MappingProblemError("mapping operation dependency graph must be acyclic")
            visiting.add(value_id)
            for operand in operations[value_id].operands:
                visit(operand)
            visiting.remove(value_id)
            visited.add(value_id)

        for operation in self.operations:
            visit(operation.id)


def _require_positive_id(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MappingProblemError(f"{label} id must be a positive integer")


def _require_phase(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MappingProblemError(f"{label} must be a non-negative integer")
