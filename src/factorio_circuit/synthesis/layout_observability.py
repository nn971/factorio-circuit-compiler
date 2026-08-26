"""Public observational wrapper for routed physical-layout optimization."""

from __future__ import annotations

from dataclasses import dataclass

from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis.layout_optimizer import (
    LayoutOptimizationProblem,
    LayoutOptimizationResult,
    optimize_physical_layout,
)
from factorio_circuit.synthesis.placement import PlacementOptions

OptimizationStats = incremental.OptimizationStats


@dataclass(frozen=True, slots=True)
class ObservedLayoutOptimizationResult:
    """Normal fail-safe optimization result plus annealer-only observational statistics."""

    optimization: LayoutOptimizationResult
    stats: OptimizationStats


def optimize_physical_layout_observed(
    problem: LayoutOptimizationProblem,
    *,
    options: PlacementOptions,
) -> ObservedLayoutOptimizationResult:
    """Optimize through the ordinary public path while collecting annealer statistics.

    Collection is context-local and does not add random draws, change proposal order, or alter the
    fail-safe result selection performed by :func:`optimize_physical_layout`.
    """

    with incremental._collect_optimization_stats() as mutable_stats:
        result = optimize_physical_layout(problem, options=options)
    return ObservedLayoutOptimizationResult(result, mutable_stats.snapshot())


__all__ = [
    "ObservedLayoutOptimizationResult",
    "OptimizationStats",
    "optimize_physical_layout_observed",
]
