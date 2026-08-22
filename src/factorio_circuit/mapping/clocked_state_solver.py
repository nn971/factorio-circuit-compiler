"""Clock-aware wrappers around the periodic state technology-mapping solvers.

The underlying CP-SAT formulations already consume candidate-owned state port offsets. This module
adds the shared physical commit resource required by every multi-tick periodic state machine and
makes the ordinary clocked state-cell candidate set the default without duplicating either solver.
"""

from __future__ import annotations

from dataclasses import replace

from .plan import PeriodicCommitResource
from .problem import MappingProblem, MappingProblemError
from .state_bus_solver import (
    solve_periodic_state_bus_mapping_problem as _solve_periodic_state_bus_mapping_problem,
)
from .state_solver import (
    PeriodicStateMappingOptimizationResult,
)
from .state_solver import (
    solve_periodic_state_mapping_problem as _solve_periodic_state_mapping_problem,
)
from .state_templates import StateCellCandidate, ordinary_state_candidates
from .templates import ImplementationCandidate


def _require_commit_period(problem: MappingProblem) -> PeriodicCommitResource:
    period = problem.period
    if period is None:
        raise MappingProblemError("periodic state solver requires a prescribed mapping period")
    if period < 3:
        raise MappingProblemError(
            "ordinary clocked state cells require period >= 3 for the shared "
            "startup/commit resource"
        )
    return PeriodicCommitResource(period=period)


def _attach_commit_resource(
    result: PeriodicStateMappingOptimizationResult,
    resource: PeriodicCommitResource,
) -> PeriodicStateMappingOptimizationResult:
    if result.plan.periodic_commit is not None:
        raise MappingProblemError("periodic state plan already contains a commit resource")
    plan = replace(
        result.plan,
        periodic_commit=resource,
        entity_cost=result.plan.entity_cost + resource.entity_cost,
    )
    return replace(result, plan=plan)


def solve_periodic_state_mapping_problem(
    problem: MappingProblem,
    *,
    candidates: tuple[ImplementationCandidate, ...] | None = None,
    state_candidates: tuple[StateCellCandidate, ...] | None = None,
    time_limit_seconds: float = 30.0,
    workers: int = 1,
) -> PeriodicStateMappingOptimizationResult:
    """Solve periodic state timing and include the shared physical commit resource in the plan."""

    resource = _require_commit_period(problem)
    selected_state_candidates = (
        state_candidates if state_candidates is not None else ordinary_state_candidates(problem)
    )
    result = _solve_periodic_state_mapping_problem(
        problem,
        candidates=candidates,
        state_candidates=selected_state_candidates,
        time_limit_seconds=time_limit_seconds,
        workers=workers,
    )
    return _attach_commit_resource(result, resource)


def solve_periodic_state_bus_mapping_problem(
    problem: MappingProblem,
    *,
    candidates: tuple[ImplementationCandidate, ...] | None = None,
    state_candidates: tuple[StateCellCandidate, ...] | None = None,
    max_delay_buses: int = 1,
    delay_bus_capacity: int = 256,
    time_limit_seconds: float = 30.0,
    workers: int = 1,
) -> PeriodicStateMappingOptimizationResult:
    """Solve periodic state + delay buses and include the shared physical commit resource."""

    resource = _require_commit_period(problem)
    selected_state_candidates = (
        state_candidates if state_candidates is not None else ordinary_state_candidates(problem)
    )
    result = _solve_periodic_state_bus_mapping_problem(
        problem,
        candidates=candidates,
        state_candidates=selected_state_candidates,
        max_delay_buses=max_delay_buses,
        delay_bus_capacity=delay_bus_capacity,
        time_limit_seconds=time_limit_seconds,
        workers=workers,
    )
    return _attach_commit_resource(result, resource)


__all__ = [
    "solve_periodic_state_bus_mapping_problem",
    "solve_periodic_state_mapping_problem",
]
