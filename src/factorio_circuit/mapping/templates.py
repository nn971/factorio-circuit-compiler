"""Target implementation candidates for the joint temporal technology mapper.

The implementation-neutral problem deliberately carries no Factorio latency. Candidate templates
own the timing equations and implementation cost. The first milestone registers the ordinary
Factorio implementations plus narrow target-specific alternatives whose timing/cost can be solved
jointly with transport.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from factorio_circuit.analysis.latency import FACTORIO_LATENCY
from factorio_circuit.ir.semantic import (
    BinaryOp,
    Compare,
    Constant,
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
    """Broad physical realization family selected by one finite candidate."""

    ORDINARY = "ordinary"
    ZERO_COST_VIEW = "zero-cost-view"
    COVERED = "covered"
    WIRE_SUM = "wire-sum"


class ImplementationRecipe(StrEnum):
    """Concrete lowering recipe within an implementation family.

    Recipes let several target realizations share the same broad implementation kind while owning
    different entity costs and timing equations.  The ordinary recipe remains the universal
    fallback; specialized recipes are only generated when their semantic preconditions are proven.
    """

    ORDINARY = "ordinary"
    SELECT_CONSTANT_FOLDED = "select-constant-folded"
    SELECT_CONSTANT_ZERO_FALSE = "select-constant-zero-false"
    DECIDER_CONDITION_COVER = "decider-condition-cover"
    COVERED_BY_DECIDER = "covered-by-decider"


@dataclass(frozen=True, slots=True)
class ImplementationCandidate:
    """One finite implementation alternative for one semantic operation.

    ``input_phase_offsets[i]`` is added to the chosen output phase to obtain the required physical
    phase of semantic operand ``i``. Ordinary Factorio combinators therefore use negative offsets;
    a zero-delay wire aggregation candidate uses zero offsets. Compile-time operands may use offset
    zero when a specialized recipe consumes their literal value during lowering rather than a
    physical wire.

    ``coupling_group`` links alternatives that must be selected together across several semantic
    operations.  This is used by multi-operation technology covers while retaining each operation's
    ordinary fallback candidate.
    """

    id: int
    operation: int
    name: str
    input_phase_offsets: tuple[int, ...]
    entity_cost: int
    output_mode: CandidateOutputMode = CandidateOutputMode.EXACT
    kind: ImplementationKind = ImplementationKind.ORDINARY
    recipe: ImplementationRecipe = ImplementationRecipe.ORDINARY
    coupling_group: int | None = None

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
            isinstance(item, bool) or not isinstance(item, int) for item in self.input_phase_offsets
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
        if not isinstance(self.recipe, ImplementationRecipe):
            raise MappingProblemError("candidate recipe must be an ImplementationRecipe")
        if self.coupling_group is not None and (
            isinstance(self.coupling_group, bool)
            or not isinstance(self.coupling_group, int)
            or self.coupling_group <= 0
        ):
            raise MappingProblemError("candidate coupling group must be a positive integer")


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


def select_constant_candidate(
    operation: MappingOperation,
    *,
    candidate_id: int,
) -> ImplementationCandidate:
    """Return a cheaper arithmetic Select realization when both arms are compile-time constants.

    ``select(c, t, f)`` is normally lowered as ``f + (t - f) * c`` using three arithmetic
    combinators.  With constant arms the delta is computed by Python elaboration instead:

    * ``f == 0`` -> ``c * t`` (one combinator, one tick);
    * otherwise -> ``c * (t - f) + f`` (two combinators, two ticks).

    The constant arms therefore have phase offset zero: they are literal parameters consumed by the
    lowering recipe, not physical inputs that need transport.
    """

    semantic = operation.semantic
    if not isinstance(semantic, Select):
        raise MappingProblemError("constant-arm Select candidate requires Select semantics")
    if not isinstance(semantic.when_true, Constant) or not isinstance(
        semantic.when_false, Constant
    ):
        raise MappingProblemError("constant-arm Select candidate requires two constant arms")
    if len(operation.operands) != 3:
        raise MappingProblemError("constant-arm Select candidate requires exactly three operands")

    stage_latency = FACTORIO_LATENCY.operation_latency("scalar_binary", "*")
    if semantic.when_false.value == 0:
        return ImplementationCandidate(
            id=candidate_id,
            operation=operation.id,
            name="select constant arms, zero false",
            input_phase_offsets=(-stage_latency, 0, 0),
            entity_cost=1,
            recipe=ImplementationRecipe.SELECT_CONSTANT_ZERO_FALSE,
        )

    return ImplementationCandidate(
        id=candidate_id,
        operation=operation.id,
        name="select constant arms, folded delta",
        input_phase_offsets=(-2 * stage_latency, 0, 0),
        entity_cost=2,
        recipe=ImplementationRecipe.SELECT_CONSTANT_FOLDED,
    )


def add_select_constant_candidates(
    problem: MappingProblem,
    candidates: tuple[ImplementationCandidate, ...],
) -> tuple[ImplementationCandidate, ...]:
    """Append every legal constant-arm Select alternative in ``problem``."""

    next_id = max((item.id for item in candidates), default=0) + 1
    result = list(candidates)
    for operation in problem.operations:
        try:
            candidate = select_constant_candidate(operation, candidate_id=next_id)
        except MappingProblemError:
            continue
        result.append(candidate)
        next_id += 1
    return tuple(result)


def _semantic_use_counts(problem: MappingProblem) -> dict[int, int]:
    """Count semantic fanout without requiring a stateless mapping problem."""

    counts: dict[int, int] = {}

    def add(value_id: int) -> None:
        counts[value_id] = counts.get(value_id, 0) + 1

    for operation in problem.operations:
        for operand in operation.operands:
            add(operand)
    for sink in problem.sinks:
        add(sink.value)
    for transition in problem.state_transitions:
        if transition.value is not None:
            add(transition.value)
        if transition.when is not None:
            add(transition.when)
    return counts


def _is_addition(value: object) -> bool:
    return isinstance(value, (BinaryOp, VectorBinaryOp)) and value.op == "+"


def wire_sum_candidate(
    problem: MappingProblem,
    operation: MappingOperation,
    *,
    candidate_id: int,
) -> ImplementationCandidate:
    """Return a conservative zero-delay same-carrier addition implementation.

    The first aggregate milestone supports scalar ``BinaryOp('+')`` and whole-vector
    ``VectorBinaryOp('+')`` roots. Both operands must be non-addition operation results and each
    operand must have exactly this one semantic use. This guarantees that the physical lowerer may
    rebind both producer output connectors onto one shared carrier without preserving an independent
    fanout network or recursively flattening another addition.
    """

    semantic = operation.semantic
    if not _is_addition(semantic):
        raise MappingProblemError("wire-sum candidate requires scalar or vector addition semantics")
    if len(operation.operands) != 2:
        raise MappingProblemError("wire-sum candidate requires exactly two operands")

    operation_ids = {item.id for item in problem.operations}
    if any(operand not in operation_ids for operand in operation.operands):
        raise MappingProblemError(
            "first wire-sum candidate requires both operands to be operation results"
        )
    producer_operations = tuple(problem.operation_by_id(item) for item in operation.operands)
    if any(_is_addition(producer.semantic) for producer in producer_operations):
        raise MappingProblemError(
            "first wire-sum candidate does not nest another semantic addition contributor"
        )

    use_counts = _semantic_use_counts(problem)
    if any(use_counts.get(operand, 0) != 1 for operand in operation.operands):
        raise MappingProblemError(
            "first wire-sum candidate requires each operand result to have exactly one use"
        )

    payload = "vector" if isinstance(semantic, VectorBinaryOp) else "scalar"
    return ImplementationCandidate(
        id=candidate_id,
        operation=operation.id,
        name=f"zero-delay {payload} wire sum",
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
