"""Public validation entry points for periodic state mapping plans."""

from __future__ import annotations

from dataclasses import replace

from .plan import PeriodicCommitResource, RealizationPlan
from .problem import MappingProblem, MappingProblemError
from .state_bus_solver import _validate_bus_state_plan
from .state_solver import validate_periodic_state_plan as _validate_periodic_state_plan
from .state_templates import StateCellCandidate
from .templates import ImplementationCandidate


def _strip_and_validate_commit(
    problem: MappingProblem,
    plan: RealizationPlan,
) -> RealizationPlan:
    resource = plan.periodic_commit
    if not isinstance(resource, PeriodicCommitResource):
        raise MappingProblemError("clocked periodic state plan requires a commit resource")
    if problem.period != resource.period:
        raise MappingProblemError("periodic commit resource period disagrees with mapping problem")
    if plan.entity_cost < resource.entity_cost:
        raise MappingProblemError("periodic commit resource exceeds total entity cost")
    return replace(
        plan,
        periodic_commit=None,
        entity_cost=plan.entity_cost - resource.entity_cost,
    )


def validate_periodic_state_plan(
    problem: MappingProblem,
    candidates: tuple[ImplementationCandidate, ...],
    state_candidates: tuple[StateCellCandidate, ...],
    plan: RealizationPlan,
) -> None:
    """Validate a private-transport periodic plan including its shared commit resource."""

    _validate_periodic_state_plan(
        problem,
        candidates,
        state_candidates,
        _strip_and_validate_commit(problem, plan),
    )


def validate_periodic_state_bus_plan(
    problem: MappingProblem,
    candidates: tuple[ImplementationCandidate, ...],
    state_candidates: tuple[StateCellCandidate, ...],
    plan: RealizationPlan,
) -> None:
    """Validate state timing, deliveries, buses, commit resource, and total plan cost."""

    _validate_bus_state_plan(
        problem,
        candidates,
        state_candidates,
        _strip_and_validate_commit(problem, plan),
    )


__all__ = ["validate_periodic_state_bus_plan", "validate_periodic_state_plan"]
