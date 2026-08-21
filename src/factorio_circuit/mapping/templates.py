"""Target implementation candidates for the joint temporal technology mapper.

The implementation-neutral problem deliberately carries no Factorio latency. Candidate templates
own the timing equations and implementation cost. The first milestone registers the ordinary
Factorio implementations plus one deliberately narrow zero-delay wire-sum candidate used to prove
that implementation choice and timing are solved together.
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
    VectorSignal,
)

from .problem import MappingOperation, MappingProblem, MappingProblemError


class CandidateOutputMode(StrEnum):
    """Availability exported by one selected physical realization."""

    EXACT = "exact"
    STABLE = "stable"
    OBSERVABLE = "observable"


class ImplementationKind(StrEnum):
    """Physical realization family selected by one finite candidate."""

    ORDINARY = "ordinary"
    ZERO_COST_VIEW = "zero-cost-view"
    WIRE_SUM = "wire-sum"


@dataclass(frozen=True, slots=True)
class ImplementationCandidate:
    """One finite implementation alternative for one semantic operation.

    ``input_phase_offsets[i]`` is added to the chosen output phase to obtain the required physical
    phase of semantic operand ``i``. Ordinary Factorio combinators therefore use negative offsets;
    a zero-delay wire aggregation candidate uses zero offsets.
    """

    id: int
    operation: int
    name: str
    input_phase_offsets: tuple[int, ...]
    entity_cost: int
    output_mode: CandidateOutputMode = CandidateOutputMode.EXACT
    kind: ImplementationKind = ImplementationKind.ORDINARY

    def __post_init__(self) -> None:
        if isinstance(self.id, bool) or not isinstance(self.id, int) or self.id <= 0:
            raise MappingProblemError("candidate id must be a positive integer")
        if (
            isinstance(self.operation, bool)
            or not isinstance(self.operation, int)
            or self.operation <= 0
        ):
            raise MappingProblemError("candidate operation id must be a positive integer")
        if not self.name:
            raise MappingProblemError("candidate name must be non-empty")
        if any(
            isinstance(item, bool) or not isinstance(item, int)
            for item in self.input_phase_offsets
        ):
            raise MappingProblemError("candidate input phase offsets must be integers")
        if any(item > 0 for item in self.input_phase_offsets):
            raise MappingProblemError("candidate cannot require an input after its output phase")
        if isinstance(self.entity_cost, bool) or not isinstance(self.entity_cost, int):
            raise MappingProblemError("candidate entity cost must be an integer")
        if self.entity_cost < 0:
            raise MappingProblemError("candidate entity cost must be non-negative")
        if not isinstance(self.output_mode, CandidateOutputMode):
            raise MappingProblemError("candidate output mode must be a CandidateOutputMode")
        if not isinstance(self.kind, ImplementationKind):
            raise MappingProblemError("candidate kind must be an ImplementationKind")


def ordinary_candidate(
    operation: MappingOperation,
    *,
    candidate_id: int,
) -> ImplementationCandidate:
    """Return the current ordinary Factorio realization of one semantic operation."""

    semantic = operation.semantic
    offsets: tuple[int, ...]
    name: str
    entity_cost = 1
    kind = ImplementationKind.ORDINARY

    if isinstance(semantic, BinaryOp):
        latency = FACTORIO_LATENCY.operation_latency("scalar_binary", semantic.op)
        offsets = (-latency, -latency)
        name = f"ordinary scalar {semantic.op}"
    elif isinstance(semantic, Compare):
        latency = FACTORIO_LATENCY.operation_latency("compare", semantic.op)
        offsets = (-latency, -latency)
        name = f"ordinary compare {semantic.op}"
    elif isinstance(semantic, Select):
        condition_latency = FACTORIO_LATENCY.operation_latency(
            "select_condition",
            semantic.name,
        )
        data_latency = FACTORIO_LATENCY.operation_latency("select_data", semantic.name)
        offsets = (-condition_latency, -data_latency, -data_latency)
        name = "ordinary select"
        entity_cost = 3
    elif isinstance(semantic, VectorSignal):
        offsets = (0,)
        name = "ordinary vector lane view"
        entity_cost = 0
        kind = ImplementationKind.ZERO_COST_VIEW
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
        entity_cost=entity_cost,
        kind=kind,
    )


def ordinary_candidates(problem: MappingProblem) -> tuple[ImplementationCandidate, ...]:
    """Generate exactly one ordinary target implementation for every semantic operation."""

    return tuple(
        ordinary_candidate(operation, candidate_id=index)
        for index, operation in enumerate(problem.operations, start=1)
    )


def wire_sum_candidate(
    problem: MappingProblem,
    operation: MappingOperation,
    *,
    candidate_id: int,
) -> ImplementationCandidate:
    """Return the first conservative zero-delay same-signal wire-sum implementation.

    This milestone only admits ``x + y`` when both operands are non-addition operation results and
    each operand has exactly this one semantic use. That restriction ensures both contributors have
    real output connectors and avoids nested aggregation until an n-way wire-resource model exists.
    """

    semantic = operation.semantic
    if not isinstance(semantic, BinaryOp) or semantic.op != "+":
        raise MappingProblemError("wire-sum candidate requires a scalar BinaryOp('+')")
    if len(operation.operands) != 2:
        raise MappingProblemError("wire-sum candidate requires exactly two operands")

    operation_ids = {item.id for item in problem.operations}
    if any(operand not in operation_ids for operand in operation.operands):
        raise MappingProblemError(
            "first wire-sum candidate requires both operands to be operation results"
        )
    producer_operations = tuple(problem.operation_by_id(item) for item in operation.operands)
    if any(
        isinstance(producer.semantic, BinaryOp) and producer.semantic.op == "+"
        for producer in producer_operations
    ):
        raise MappingProblemError(
            "first wire-sum candidate does not nest another semantic addition contributor"
        )

    use_counts: dict[int, int] = {}
    for use in problem.uses():
        use_counts[use.producer] = use_counts.get(use.producer, 0) + 1
    if any(use_counts.get(operand, 0) != 1 for operand in operation.operands):
        raise MappingProblemError(
            "first wire-sum candidate requires each operand result to have exactly one use"
        )

    return ImplementationCandidate(
        id=candidate_id,
        operation=operation.id,
        name="zero-delay wire sum",
        input_phase_offsets=(0, 0),
        entity_cost=0,
        kind=ImplementationKind.WIRE_SUM,
    )


def add_wire_sum_candidates(
    problem: MappingProblem,
    candidates: tuple[ImplementationCandidate, ...],
) -> tuple[ImplementationCandidate, ...]:
    """Append every conservative wire-sum alternative that is legal in ``problem``."""

    next_id = max((item.id for item in candidates), default=0) + 1
    result = list(candidates)
    for operation in problem.operations:
        try:
            candidate = wire_sum_candidate(problem, operation, candidate_id=next_id)
        except MappingProblemError:
            continue
        result.append(candidate)
        next_id += 1
    return tuple(result)
