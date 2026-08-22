"""Conservative multi-operation decider-condition covers for temporal mapping.

The current candidate model chooses one implementation per semantic operation.  A Factorio decider
can nevertheless realize an entire homogeneous boolean tree such as ``c0 | c1 | c2 | c3`` when all
leaves are scalar comparisons.  This module performs a deliberately narrow candidate-set rewrite:

* only homogeneous ``|`` or ``&`` trees are considered;
* every internal node below the root must have exactly one semantic use, so nothing outside the
  cover can observe an omitted intermediate boolean value;
* only maximal non-overlapping covers are installed;
* the root becomes one one-tick decider candidate;
* covered compare/boolean nodes become zero-cost, zero-latency phantom candidates whose timing
  equations propagate the decider input phase to the original comparison operands.

Because the admitted shape has an exact Factorio realization, the first milestone replaces the
ordinary candidates inside that proven-safe tree rather than introducing solver-level optional
multi-operation coupling.  Non-coverable shapes retain their ordinary candidates unchanged.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from factorio_circuit.analysis.latency import FACTORIO_LATENCY
from factorio_circuit.ir.semantic import BinaryOp, Compare

from .problem import MappingOperation, MappingProblem
from .templates import (
    ImplementationCandidate,
    ImplementationKind,
    ImplementationRecipe,
)

_BOOLEAN_OPS = frozenset({"|", "&"})
_COMPARE_OPS = frozenset({"==", "!=", "<", "<=", ">", ">="})


@dataclass(frozen=True, slots=True)
class DeciderConditionCover:
    """One maximal homogeneous boolean tree realizable by a single Factorio decider."""

    root_operation: int
    boolean_op: str
    operation_ids: tuple[int, ...]
    comparisons: tuple[Compare, ...]

    @property
    def internal_operation_ids(self) -> tuple[int, ...]:
        return tuple(item for item in self.operation_ids if item != self.root_operation)


def flatten_decider_condition_cover(value: object) -> tuple[str, tuple[Compare, ...]] | None:
    """Return ``(boolean_op, comparisons)`` for one homogeneous compare tree."""

    if not isinstance(value, BinaryOp) or value.op not in _BOOLEAN_OPS:
        return None
    boolean_op = value.op
    comparisons: list[Compare] = []

    def visit(node: object) -> bool:
        if isinstance(node, Compare):
            if node.op not in _COMPARE_OPS:
                return False
            comparisons.append(node)
            return True
        if isinstance(node, BinaryOp) and node.op == boolean_op:
            return visit(node.left) and visit(node.right)
        return False

    if not visit(value) or len(comparisons) < 2:
        return None
    return boolean_op, tuple(comparisons)


def _use_counts(problem: MappingProblem) -> Counter[int]:
    counts: Counter[int] = Counter()
    for operation in problem.operations:
        counts.update(operation.operands)
    counts.update(sink.value for sink in problem.sinks)
    for transition in problem.state_transitions:
        if transition.value is not None:
            counts[transition.value] += 1
        if transition.when is not None:
            counts[transition.when] += 1
    return counts


def _candidate_cover(
    problem: MappingProblem,
    operation: MappingOperation,
    *,
    semantic_id_to_operation: dict[int, int],
    use_counts: Counter[int],
) -> DeciderConditionCover | None:
    flattened = flatten_decider_condition_cover(operation.semantic)
    if flattened is None:
        return None
    boolean_op, comparisons = flattened

    operation_ids: list[int] = []

    def collect(node: object) -> bool:
        operation_id = semantic_id_to_operation.get(id(node))
        if operation_id is None:
            return False
        operation_ids.append(operation_id)
        if isinstance(node, Compare):
            return True
        if isinstance(node, BinaryOp) and node.op == boolean_op:
            return collect(node.left) and collect(node.right)
        return False

    if not collect(operation.semantic):
        return None
    unique_ids = tuple(dict.fromkeys(operation_ids))
    if len(unique_ids) != len(operation_ids):
        # Shared DAG nodes need optional-cover coupling; keep this milestone tree-only.
        return None
    if any(use_counts[item] != 1 for item in unique_ids if item != operation.id):
        return None
    return DeciderConditionCover(
        root_operation=operation.id,
        boolean_op=boolean_op,
        operation_ids=unique_ids,
        comparisons=comparisons,
    )


def find_decider_condition_covers(problem: MappingProblem) -> tuple[DeciderConditionCover, ...]:
    """Find maximal non-overlapping safe boolean-tree covers in ``problem``."""

    semantic_id_to_operation = {id(item.semantic): item.id for item in problem.operations}
    use_counts = _use_counts(problem)
    possible = [
        cover
        for operation in problem.operations
        if (
            cover := _candidate_cover(
                problem,
                operation,
                semantic_id_to_operation=semantic_id_to_operation,
                use_counts=use_counts,
            )
        )
        is not None
    ]

    operation_sets = {cover.root_operation: frozenset(cover.operation_ids) for cover in possible}
    maximal = [
        cover
        for cover in possible
        if not any(
            operation_sets[cover.root_operation] < operation_sets[other.root_operation]
            for other in possible
            if other.root_operation != cover.root_operation
        )
    ]

    # The exclusive-internal-use rule should make maximal covers disjoint. Keep an explicit guard
    # because silently replacing overlapping operation candidates would make plan accounting opaque.
    occupied: set[int] = set()
    result: list[DeciderConditionCover] = []
    for cover in sorted(maximal, key=lambda item: item.root_operation):
        if occupied.intersection(cover.operation_ids):
            continue
        occupied.update(cover.operation_ids)
        result.append(cover)
    return tuple(result)


def add_decider_condition_cover_candidates(
    problem: MappingProblem,
    candidates: tuple[ImplementationCandidate, ...],
) -> tuple[ImplementationCandidate, ...]:
    """Replace proven-safe homogeneous boolean trees by exact decider-cover candidates."""

    covers = find_decider_condition_covers(problem)
    if not covers:
        return candidates

    next_id = max((item.id for item in candidates), default=0) + 1
    replacements: dict[int, ImplementationCandidate] = {}
    for cover in covers:
        root = problem.operation_by_id(cover.root_operation)
        latency = FACTORIO_LATENCY.operation_latency("compare", cover.boolean_op)
        replacements[root.id] = ImplementationCandidate(
            id=next_id,
            operation=root.id,
            name=f"decider {cover.boolean_op} condition cover ({len(cover.comparisons)} leaves)",
            input_phase_offsets=(-latency,) * len(root.operands),
            entity_cost=1,
            recipe=ImplementationRecipe.DECIDER_CONDITION_COVER,
        )
        next_id += 1

        for operation_id in cover.internal_operation_ids:
            operation = problem.operation_by_id(operation_id)
            replacements[operation_id] = ImplementationCandidate(
                id=next_id,
                operation=operation_id,
                name=f"covered by decider root {root.id}",
                input_phase_offsets=(0,) * len(operation.operands),
                entity_cost=0,
                kind=ImplementationKind.COVERED,
                recipe=ImplementationRecipe.COVERED_BY_DECIDER,
            )
            next_id += 1

    result = [item for item in candidates if item.operation not in replacements]
    result.extend(replacements[item] for item in sorted(replacements))
    return tuple(result)


__all__ = [
    "DeciderConditionCover",
    "add_decider_condition_cover_candidates",
    "find_decider_condition_covers",
    "flatten_decider_condition_cover",
]
