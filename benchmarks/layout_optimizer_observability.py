"""Opt-in annealer observability report over representative Milestone A corpus cases."""

from __future__ import annotations

import argparse
from dataclasses import replace

from benchmarks.layout_optimizer_corpus import (
    _fixed_endpoint_span_case,
    _narrow_corridor_case,
    _perimeter_anchor_case,
    _shared_bus_case,
)
from benchmarks.layout_optimizer_topology_corpus import (
    _near_optimal_packed_case,
    _red_green_mesh_case,
)
from factorio_circuit.synthesis.layout_observability import optimize_physical_layout_observed
from factorio_circuit.synthesis.layout_optimizer import validate_physical_layout
from factorio_circuit.synthesis.placement import PlacementOptions


def _run_case(case, *, proposals: int, seed: int) -> None:
    validate_physical_layout(case.problem)
    observed = optimize_physical_layout_observed(
        case.problem,
        options=PlacementOptions(
            anchor_io=False,
            reserve_corridors=False,
            iterations=proposals,
            random_seed=seed,
            restarts=1,
        ),
    )
    result = observed.optimization
    stats = observed.stats
    validate_physical_layout(replace(case.problem, layout=result.layout))
    if stats.accounted_proposals != stats.proposals_attempted:
        raise AssertionError(
            f"{case.name} observability counters do not account for every proposal"
        )
    if stats.classified_relay_deletions != stats.relay_deletions:
        raise AssertionError(f"{case.name} relay simplification counters are inconsistent")
    if stats.classified_topology_rebuild_attempts != stats.topology_rebuild_attempts:
        raise AssertionError(f"{case.name} topology rebuild counters are inconsistent")
    if stats.classified_adaptive_rebuild_attempts != stats.adaptive_topology_rebuild_attempts:
        raise AssertionError(f"{case.name} adaptive rebuild counters are inconsistent")

    print(
        f"{case.name} seed={seed}: "
        f"attempted={stats.proposals_attempted}, accepted={stats.accepted_moves}, "
        f"reject(noop={stats.noop_rejections}, geometry={stats.geometry_rejections}, "
        f"reach={stats.wire_reach_rejections}, metropolis={stats.metropolis_rejections}), "
        f"proposal-kind(implementation={stats.implementation_proposals}, "
        f"relay={stats.relay_proposals}), swaps={stats.swaps_accepted}/{stats.swap_attempts}, "
        f"simplify(calls={stats.simplification_calls}, total={stats.relay_deletions}, "
        f"isolated={stats.relay_isolated_deletions}, leaf={stats.relay_leaf_deletions}, "
        f"bypass={stats.relay_degree_two_bypasses}), "
        f"routing(searches={stats.routing_search_calls}, "
        f"negotiated={stats.negotiated_routing_search_calls}, "
        f"failed={stats.routing_search_failures}, queue-pops={stats.routing_queue_pops}), "
        f"rebuilds={stats.topology_rebuild_successes}/{stats.topology_rebuild_attempts} "
        f"(scheduled={stats.scheduled_topology_rebuild_attempts}, "
        f"adaptive={stats.adaptive_topology_rebuild_attempts}, "
        f"pressure={stats.wire_reach_pressure_rebuild_attempts}, "
        f"stagnation={stats.stagnation_rebuild_attempts}), "
        f"epochs={stats.epochs_completed}, stagnation={stats.epochs_since_last_improvement}, "
        f"objective={result.before.objective}->{result.after.objective}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposals", type=int, default=256)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    if args.proposals < 0:
        parser.error("--proposals must be non-negative")

    for case in (
        _shared_bus_case(),
        _fixed_endpoint_span_case(),
        _narrow_corridor_case(),
        _perimeter_anchor_case(),
        _red_green_mesh_case(),
        _near_optimal_packed_case(),
    ):
        _run_case(case, proposals=args.proposals, seed=args.seed)


if __name__ == "__main__":
    main()
