"""Paired acceptance probe for the measured relay-relief epoch policy."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
from statistics import median
from time import perf_counter
from typing import Iterator

from benchmarks.layout_optimizer_corpus import (
    _fixed_endpoint_span_case,
    _narrow_corridor_case,
    _perimeter_anchor_case,
    _relay_forest_case,
    _shared_bus_case,
)
from benchmarks.layout_optimizer_topology_corpus import (
    _clustered_sparse_cut_case,
    _near_optimal_packed_case,
    _red_green_mesh_case,
)
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis.layout_observability import optimize_physical_layout_observed
from factorio_circuit.synthesis.layout_optimizer import validate_physical_layout
from factorio_circuit.synthesis.placement import PlacementOptions

CASES = (
    ("relay-forest", _relay_forest_case),
    ("shared-bus", _shared_bus_case),
    ("clustered-sparse-cut", _clustered_sparse_cut_case),
    ("fixed-endpoint-span", _fixed_endpoint_span_case),
    ("narrow-corridor", _narrow_corridor_case),
    ("perimeter-anchors", _perimeter_anchor_case),
    ("red-green-mesh", _red_green_mesh_case),
    ("near-optimal-packed", _near_optimal_packed_case),
)


@contextmanager
def _relief_enabled(enabled: bool) -> Iterator[None]:
    original = incremental._should_schedule_relay_relief
    if not enabled:
        incremental._should_schedule_relay_relief = lambda *args, **kwargs: False
    try:
        yield
    finally:
        incremental._should_schedule_relay_relief = original


def _run(factory, *, proposals: int, seed: int, relief: bool):
    case = factory()
    validate_physical_layout(case.problem)
    options = PlacementOptions(
        anchor_io=False,
        reserve_corridors=False,
        iterations=proposals,
        random_seed=seed,
        restarts=1,
    )
    started = perf_counter()
    with _relief_enabled(relief):
        observed = optimize_physical_layout_observed(case.problem, options=options)
    elapsed = perf_counter() - started
    validate_physical_layout(replace(case.problem, layout=observed.optimization.layout))
    return observed, elapsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=8)
    parser.add_argument("--proposals", type=int, default=4096)
    args = parser.parse_args()
    if args.seeds <= 0:
        parser.error("--seeds must be positive")
    if args.proposals <= 0:
        parser.error("--proposals must be positive")

    better = equal = worse = 0
    runtime_ratios: list[float] = []
    aggregate = {
        "relay_proposals": 0,
        "relay_accepted": 0,
        "reach_rejections": 0,
        "accepted_moves": 0,
    }

    for name, factory in CASES:
        for seed in range(args.seeds):
            baseline, baseline_time = _run(
                factory,
                proposals=args.proposals,
                seed=seed,
                relief=False,
            )
            candidate, candidate_time = _run(
                factory,
                proposals=args.proposals,
                seed=seed,
                relief=True,
            )
            before = baseline.optimization.after.objective
            after = candidate.optimization.after.objective
            if after < before:
                outcome = "better"
                better += 1
            elif after > before:
                outcome = "worse"
                worse += 1
            else:
                outcome = "equal"
                equal += 1

            base_stats = baseline.stats
            cand_stats = candidate.stats
            runtime_ratio = candidate_time / baseline_time if baseline_time else 1.0
            runtime_ratios.append(runtime_ratio)
            deltas = {
                "relay_proposals": cand_stats.relay_proposals - base_stats.relay_proposals,
                "relay_accepted": cand_stats.relay_moves_accepted - base_stats.relay_moves_accepted,
                "reach_rejections": (
                    cand_stats.wire_reach_rejections - base_stats.wire_reach_rejections
                ),
                "accepted_moves": cand_stats.accepted_moves - base_stats.accepted_moves,
            }
            for field, value in deltas.items():
                aggregate[field] += value

            print(
                f"{name:22} seed={seed} {outcome:6} "
                f"{before}->{after}; "
                f"relay-proposals {base_stats.relay_proposals}->{cand_stats.relay_proposals}; "
                f"relay-accepted {base_stats.relay_moves_accepted}->"
                f"{cand_stats.relay_moves_accepted}; "
                f"reach-delta={deltas['reach_rejections']:+d}; "
                f"accepted-delta={deltas['accepted_moves']:+d}; "
                f"runtime=x{runtime_ratio:.3f}"
            )

    print()
    print(f"OVERALL better/equal/worse={better}/{equal}/{worse}")
    print(f"median-runtime-ratio={median(runtime_ratios):.3f}")
    print(
        "aggregate-delta "
        + ", ".join(f"{field}={value:+d}" for field, value in aggregate.items())
    )
    if worse:
        raise SystemExit("relay-relief experiment rejected: candidate worsened an immediate-parent objective")


if __name__ == "__main__":
    main()
