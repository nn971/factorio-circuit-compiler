"""Target implementation candidates for the joint temporal technology mapper.

The implementation-neutral problem deliberately carries no Factorio latency.  Candidate templates
own the timing equations and implementation cost.  The first milestone registers only the ordinary
Factorio implementations; later milestones can add zero-delay wire aggregation, fusion,
rematerialization, buses, and reusable functional units without changing semantic causality.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from factorio_circuit.analysis.latency import FACTORIO_LATENCY
from factorio_circuit.ir.semantic import (
    BinaryOp,
    Compare,
    Select,
    VectorBinaryOp,
    VectorFilter,
    VectorScalarOp,
    VectorSelect,
)

from .problem import MappingOperation, MappingProblem, MappingProblemError


class CandidateOutputMode(StrEnum):
    """Availability exported by one selected physical realization.

    The initial ordinary implementation family is conservatively EXACT.  Keeping the contract on
    the candidate makes later observability-preserving or stable implementations an implementation
    choice rather than an upstream timing assumption.
    """

    EXACT = "exact"
    STABLE = "stable"
    OBSERVABLE = "observable"


@dataclass(frozen=True, slots=True)
class ImplementationCandidate:
    """One finite implementation alternative for one semantic operation.

    ``input_phase_offsets[i]`` is added to the chosen output phase to obtain the required physical
    phase of semantic operand ``i``.  Ordinary Factorio combinators therefore use negative offsets;
    a future zero-delay wire aggregation candidate will use zero offsets.
    """

    id: int
    operation: int
    name: str
    input_phase_offsets: tuple[int, ...]
    entity_cost: int
    output_mode: CandidateOutputMode = CandidateOutputMode.EXACT

    def __post_init__(self) -> None:
        if isinstance(self.id, bool) or not isinstance(self.id, int) or self.id <= 0:
            raise MappingProblemError("candidate id must be a positive integer")
        if isinstance(self.operation, bool) or not isinstance(self.operation, int) or self.operation <= 0:
            raise MappingProblemError("candidate operation id must be a positive integer")
        if not self.name:
            raise MappingProblemError("candidate name must be non-empty")
        if any(isinstance(item, bool) or not isinstance(item, int) for item in self.input_phase_offsets):
            raise MappingProblemError("candidate input phase offsets must be integers")
        if any(item > 0 for item in self.input_phase_offsets):
            raise MappingProblemError("candidate cannot require an input after its output phase")
        if isinstance(self.entity_cost, bool) or not isinstance(self.entity_cost, int):
            raise MappingProblemError("candidate entity cost must be an integer")
        if self.entity_cost < 0:
            raise MappingProblemError("candidate entity cost must be non-negative")
        if not isinstance(self.output_mode, CandidateOutputMode):
            raise MappingProblemError("candidate output mode must be a CandidateOutputMode")


def ordinary_candidate(operation: MappingOperation, *, candidate_id: int) -> ImplementationCandidate:
    """Return the current ordinary Factorio realization of one semantic operation."""

    semantic = operation.semantic
    offsets: tuple[int, ...]
    name: str

    if isinstance(semantic, BinaryOp):
        latency = FACTORIO_LATENCY.operation_latency("scalar_binary", semantic.op)
        offsets = (-latency, -latency)
        name = f"ordinary scalar {semantic.op}"
    elif isinstance(semantic, Compare):
        latency = FACTORIO_LATENCY.operation_latency("compare", semantic.op)
        offsets = (-latency, -latency)
        name = f"ordinary compare {semantic.op}"
    elif isinstance(semantic, Select):
        condition_latency = FACTORIO_LATENCY.operation_latency("select_condition", semantic.name)
        data_latency = FACTORIO_LATENCY.operation_latency("select_data", semantic.name)
        offsets = (-condition_latency, -data_latency, -data_latency)
        name = "ordinary select"
    elif isinstance(semantic, VectorBinaryOp):
        latency = FACTORIO_LATENCY.operation_latency("vector_binary", semantic.op)
        offsets = (-latency, -latency)
        name = f"ordinary vector {semantic.op}"
    elif isinstance(semantic, VectorScalarOp):
        latency = FACTORIO_LATENCY.operation_latency("vector_scalar", semantic.op)
        offsets = (-latency, -latency)
        name = f"ordinary vector-scalar {semantic.op}"
    elif isinstance(semantic, VectorFilter):
        latency = FACTORIO_LATENCY.operation_latency("vector_filter", semantic.op)
        offsets = (-latency,)
        name = f"ordinary vector-filter {semantic.op}"
    elif isinstance(semantic, VectorSelect):
        latency = FACTORIO_LATENCY.operation_latency("vector_select", semantic.op)
        offsets = (-latency,)
        name = f"ordinary vector-select {semantic.op}"
    else:
        raise MappingProblemError(
            f"no ordinary mapping candidate for semantic type {type(semantic).__name__}"
        )

    if len(offsets) != len(operation.operands):
        raise MappingProblemError(
            f"ordinary candidate for {operation.label!r} expects {len(offsets)} operands, "
            f"but the mapping problem supplies {len(operation.operands)}"
        )
    return ImplementationCandidate(
        id=candidate_id,
        operation=operation.id,
        name=name,
        input_phase_offsets=offsets,
        entity_cost=1,
    )


def ordinary_candidates(problem: MappingProblem) -> tuple[ImplementationCandidate, ...]:
    """Generate exactly one ordinary target implementation for every semantic operation."""

    return tuple(
        ordinary_candidate(operation, candidate_id=index)
        for index, operation in enumerate(problem.operations, start=1)
    )
