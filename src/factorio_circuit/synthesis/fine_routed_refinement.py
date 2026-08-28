"""Transactional fine refinement of an already-valid routed physical layout.

C7 deliberately starts after a successful routing checkpoint. It reuses the reach-safe joint
annealer directly, so implementation entities and relay combinators may move together while every
accepted hot-loop move preserves wire reach. Unlike the generic optimizer entry point, this stage
does not run another coarse placement/reseed before annealing.

The input physical artifact remains the fallback. A refined candidate is materialized, exact-
validated, and committed only when it strictly improves the public lexicographic physical objective.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis import layout_optimizer
from factorio_circuit.synthesis.layout import Layout
from factorio_circuit.synthesis.layout_optimizer import (
    LayoutOptimizationProblem,
    PhysicalLayoutMetrics,
    physical_layout_metrics,
)
from factorio_circuit.synthesis.placement import PlacementOptions


@dataclass(frozen=True, slots=True)
class FineRefinementOptions:
    """Bounded controls for one routed fine-annealing transaction."""

    proposals: int = 4096
    random_seed: int = 0


@dataclass(frozen=True, slots=True)
class FineRefinementResult:
    """Validated improvement or the exact original routed fallback."""

    layout: Layout
    before: PhysicalLayoutMetrics
    after: PhysicalLayoutMetrics
    proposal_budget: int
    accepted: bool
    diagnostics: tuple[str, ...] = ()


def refine_routed_layout_transactionally(
    problem: LayoutOptimizationProblem,
    *,
    options: FineRefinementOptions | None = None,
) -> FineRefinementResult:
    """Fine-anneal one valid routed layout without invoking coarse compaction again."""

    if options is None:
        options = FineRefinementOptions()
    if options.proposals < 0:
        raise ValueError("proposals must be non-negative")

    validated = layout_optimizer._validated_embedding(problem)
    before = physical_layout_metrics(problem.layout)
    if options.proposals == 0:
        return FineRefinementResult(
            problem.layout,
            before,
            before,
            0,
            False,
        )

    state = validated.state
    topology = validated.topology
    grid = layout_optimizer._lattice_grid(problem.lattice)
    anneal_options = PlacementOptions(
        anchor_io=False,
        iterations=options.proposals,
        random_seed=options.random_seed,
        restarts=1,
    )
    diagnostics: list[str] = []
    try:
        topology = incremental._anneal_feasible(
            state,
            topology,
            anneal_options,
            grid,
            diagnostics,
        )
        topology = incremental._simplify_feasible_topology(state, topology)
        candidate = layout_optimizer._materialize_layout(
            problem.layout,
            state,
            topology.routing,
        )
        layout_optimizer._validated_embedding(replace(problem, layout=candidate))
    except ValueError as exc:
        diagnostics.append(f"fine refinement candidate rejected: {exc}")
        return FineRefinementResult(
            problem.layout,
            before,
            before,
            options.proposals,
            False,
            tuple(diagnostics),
        )

    after = physical_layout_metrics(candidate)
    if after.objective >= before.objective:
        if after.objective > before.objective:
            diagnostics.append("fine refinement was valid but worsened the physical objective")
        else:
            diagnostics.append(
                "fine refinement was valid but did not improve the physical objective"
            )
        return FineRefinementResult(
            problem.layout,
            before,
            before,
            options.proposals,
            False,
            tuple(diagnostics),
        )

    return FineRefinementResult(
        candidate,
        before,
        after,
        options.proposals,
        True,
        tuple(diagnostics),
    )
