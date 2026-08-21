"""Implementation-neutral problem records for temporal technology mapping.

This layer contains only semantic data dependencies plus target-independent occurrence boundaries.
It deliberately has no Factorio combinator latency.  Latency first appears in implementation
candidates under :mod:`factorio_circuit.mapping.templates`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from factorio_circuit.ir.semantic import PayloadShape


class MappingProblemError(ValueError):
    """Raised when an implementation-neutral mapping problem is malformed."""


class MappingSourceMode(StrEnum):
    """Physical observation contract supplied for one semantic leaf.

    These modes describe target/source capability, not the latency of any implementation of a
    semantic operation.
    """

    STABLE = "stable"
    OBSERVABLE = "observable"
    EXACT = "exact"


@dataclass(frozen=True, slots=True)
class MappingSource:
    id: int
    label: str
    shape: PayloadShape
    mode: MappingSourceMode
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
        if any(not isinstance(item, int) or isinstance(item, bool) or item <= 0 for item in self.operands):
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

    def __post_init__(self) -> None:
        if isinstance(self.horizon, bool) or not isinstance(self.horizon, int) or self.horizon < 0:
            raise MappingProblemError("mapping horizon must be a non-negative integer")
        self.validate()

    @property
    def value_ids(self) -> frozenset[int]:
        return frozenset((*[item.id for item in self.sources], *[item.id for item in self.operations]))

    def source_by_id(self, value_id: int) -> MappingSource:
        for source in self.sources:
            if source.id == value_id:
                return source
        raise KeyError(value_id)

    def operation_by_id(self, value_id: int) -> MappingOperation:
        for operation in self.operations:
            if operation.id == value_id:
                return operation
        raise KeyError(value_id)

    def uses(self) -> tuple[MappingUse, ...]:
        result = [
            MappingUse(producer, operation.id, operand_index)
            for operation in self.operations
            for operand_index, producer in enumerate(operation.operands)
        ]
        result.extend(MappingUse(sink.value, sink.id, None) for sink in self.sinks)
        return tuple(result)

    def validate(self) -> None:
        value_ids = [item.id for item in self.sources] + [item.id for item in self.operations]
        sink_ids = [item.id for item in self.sinks]
        if len(set(value_ids)) != len(value_ids):
            raise MappingProblemError("mapping value ids must be unique")
        if len(set(sink_ids)) != len(sink_ids):
            raise MappingProblemError("mapping sink ids must be unique")
        if set(value_ids) & set(sink_ids):
            raise MappingProblemError("mapping value and sink ids must use disjoint namespaces")

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
