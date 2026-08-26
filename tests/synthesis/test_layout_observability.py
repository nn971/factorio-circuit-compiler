from __future__ import annotations

from benchmarks.layout_optimizer_corpus import _fixed_endpoint_span_case, _shared_bus_case
from benchmarks.layout_optimizer_topology_corpus import _near_optimal_packed_case
from factorio_circuit.synthesis.layout_observability import (
    OptimizationStats,
    optimize_physical_layout_observed,
)
from factorio_circuit.synthesis.layout_optimizer import optimize_physical_layout
from factorio_circuit.synthesis.placement import PlacementOptions


def _options(*, iterations: int) -> PlacementOptions:
    return PlacementOptions(
        anchor_io=False,
        reserve_corridors=False,
        iterations=iterations,
        random_seed=17,
        restarts=1,
    )


def test_observability_preserves_production_annealer_artifact() -> None:
    case = _shared_bus_case()
    options = _options(iterations=96)

    baseline = optimize_physical_layout(case.problem, options=options)
    observed = optimize_physical_layout_observed(case.problem, options=options)

    assert observed.optimization == baseline


def test_observability_accounts_for_every_proposal() -> None:
    case = _shared_bus_case()
    observed = optimize_physical_layout_observed(case.problem, options=_options(iterations=96))
    stats = observed.stats

    assert stats.proposals_attempted > 0
    assert stats.accounted_proposals == stats.proposals_attempted
    assert stats.implementation_proposals + stats.relay_proposals == stats.proposals_attempted
    assert stats.implementation_moves_accepted + stats.relay_moves_accepted == stats.accepted_moves
    assert stats.swaps_accepted <= stats.swap_attempts
    assert stats.reach_clip_moves_accepted <= stats.reach_clip_feasible_targets
    assert stats.reach_clip_feasible_targets <= stats.reach_clip_attempts
    assert stats.classified_relay_deletions == stats.relay_deletions
    assert stats.negotiated_routing_search_calls <= stats.routing_search_calls
    assert stats.routing_search_failures <= stats.routing_search_calls
    assert len(stats.best_objective_history) == stats.epochs_completed + 1
    assert all(
        later <= earlier
        for earlier, later in zip(
            stats.best_objective_history,
            stats.best_objective_history[1:],
            strict=False,
        )
    )


def test_observability_reports_routing_search_work_without_changing_artifact() -> None:
    case = _fixed_endpoint_span_case()
    options = _options(iterations=257)

    baseline = optimize_physical_layout(case.problem, options=options)
    observed = optimize_physical_layout_observed(case.problem, options=options)
    stats = observed.stats

    assert observed.optimization == baseline
    assert stats.simplification_calls > 0
    assert stats.routing_search_calls > 0
    assert stats.routing_queue_pops >= stats.routing_search_calls
    assert stats.classified_relay_deletions == stats.relay_deletions


def test_zero_budget_observability_is_empty() -> None:
    case = _near_optimal_packed_case()
    observed = optimize_physical_layout_observed(case.problem, options=_options(iterations=0))

    assert observed.optimization.layout == case.problem.layout
    assert observed.stats == OptimizationStats()
