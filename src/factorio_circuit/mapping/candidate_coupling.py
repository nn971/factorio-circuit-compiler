"""Cross-operation implementation-candidate coupling.

Most implementation choices are local to one semantic operation. Multi-operation technology covers
need a small additional contract: either every candidate in one coupling group is selected, or none
of them is. This keeps ordinary per-operation fallbacks available while making an aggregate target
implementation atomic from the solver's point of view.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .plan import RealizationPlan
from .problem import MappingProblem, MappingProblemError
from .templates import ImplementationCandidate


def candidate_coupling_groups(
    candidates: tuple[ImplementationCandidate, ...],
) -> tuple[tuple[ImplementationCandidate, ...], ...]:
    """Return coupled candidate groups in deterministic group-id order."""

    grouped: dict[int, list[ImplementationCandidate]] = defaultdict(list)
    for candidate in candidates:
        if candidate.coupling_group is not None:
            grouped[candidate.coupling_group].append(candidate)
    return tuple(
        tuple(sorted(grouped[group], key=lambda item: (item.operation, item.id)))
        for group in sorted(grouped)
    )


def validate_candidate_coupling_groups(
    problem: MappingProblem,
    candidates: tuple[ImplementationCandidate, ...],
) -> None:
    """Validate the static structure of cross-operation candidate groups."""

    operations = {item.id for item in problem.operations}
    for group in candidate_coupling_groups(candidates):
        group_id = group[0].coupling_group
        if len(group) < 2:
            raise MappingProblemError(
                f"candidate coupling group {group_id} must span at least two operations"
            )
        group_operations = [item.operation for item in group]
        if len(set(group_operations)) != len(group_operations):
            raise MappingProblemError(
                f"candidate coupling group {group_id} has multiple members for one operation"
            )
        if not set(group_operations) <= operations:
            raise MappingProblemError(
                f"candidate coupling group {group_id} references an unknown operation"
            )


def add_candidate_coupling_constraints(
    model: Any,
    choose: dict[int, Any],
    candidates: tuple[ImplementationCandidate, ...],
) -> None:
    """Require every member of each coupling group to share one selection bit."""

    for group in candidate_coupling_groups(candidates):
        anchor = choose[group[0].id]
        for candidate in group[1:]:
            model.Add(choose[candidate.id] == anchor)


def validate_selected_candidate_coupling(
    candidates: tuple[ImplementationCandidate, ...],
    plan: RealizationPlan,
) -> None:
    """Reject plans that select only part of a coupled implementation cover."""

    selected_ids = {item.candidate for item in plan.realizations}
    for group in candidate_coupling_groups(candidates):
        selected = [item.id in selected_ids for item in group]
        if any(selected) and not all(selected):
            raise MappingProblemError(
                f"realization plan partially selects candidate coupling group "
                f"{group[0].coupling_group}"
            )


__all__ = [
    "add_candidate_coupling_constraints",
    "candidate_coupling_groups",
    "validate_candidate_coupling_groups",
    "validate_selected_candidate_coupling",
]
