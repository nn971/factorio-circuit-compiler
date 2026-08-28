"""Transactional fine refinement of an already-valid routed physical layout.

C7 deliberately starts after a successful routing checkpoint. It reuses the reach-safe joint
annealer directly, so implementation entities and relay combinators may move together while every
accepted hot-loop move preserves wire reach. Unlike the generic optimizer entry point, this stage
does not run another coarse placement/reseed before annealing.

Fine work is split into sub-epoch chunks shorter than the joint annealer's 256-proposal topology
epoch. That intentionally suppresses its scheduled full reroutes: C5 already owns expensive global
routing transactions, while C7 should spend its budget on local joint motion and relay bypasses.

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
    chunk_size: int = incremental._EPOCH_PROPOSALS - 1


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
    if not 0 < options.chunk_size < incremental._EPOCH_PROPOSALS:
        raise ValueError(
            f"chunk_size must be in [1, {incremental._EPOCH_PROPOSALS - 1}] "
            "to keep C7 local"
        )

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
    diagnostics: list[str] = []
    try:
        remaining = options.proposals
        chunk_index = 0
        while remaining > 0:
            chunk = min(options.chunk_size, remaining)
            anneal_options = PlacementOptions(
                anchor_io=False,
                iterations=chunk,
                random_seed=options.random_seed + chunk_index,
                restarts=1,
            )
            topology = incremental._anneal_feasible(
                state,
                topology,
                anneal_options,
                grid,
                diagnostics,
            )
            remaining -= chunk
            chunk_index += 1

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
