"""Public validation entry points for periodic state mapping plans."""

from __future__ import annotations

from .plan import RealizationPlan
from .problem import MappingProblem
from .state_bus_solver import _validate_bus_state_plan
from .state_templates import StateCellCandidate
from .templates import ImplementationCandidate


def validate_periodic_state_bus_plan(
    problem: MappingProblem,
    candidates: tuple[ImplementationCandidate, ...],
    state_candidates: tuple[StateCellCandidate, ...],
    plan: RealizationPlan,
) -> None:
    """Validate candidate timing, state windows, deliveries, buses, and plan cost."""

    _validate_bus_state_plan(problem, candidates, state_candidates, plan)


__all__ = ["validate_periodic_state_bus_plan"]
