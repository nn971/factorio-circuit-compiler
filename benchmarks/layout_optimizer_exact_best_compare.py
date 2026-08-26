"""Compare epoch-only best tracking with local exact accepted-move tracking."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from statistics import median
from time import perf_counter
from typing import Any

from benchmarks.layout_optimizer_corpus import (
    _fixed_endpoint_span_case,
    _narrow_corridor_case,
    _perimeter_anchor_case,
    _relay_forest_case,
    _shared_bus_case,
)
from benchmarks.layout_optimizer_topology_corpus import _clustered_sparse_cut_case
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis.layout_observability import optimize_physical_layout_observed
from factorio_circuit.synthesis.layout_optimizer import validate_physical_layout
from factorio_circuit.synthesis.placement import PlacementOptions

CaseFactory = Callable[[], Any]
_TRAJECTORY_FIELDS = (
    "proposals_attempted",
    "accepted_moves",
    "noop_rejections",
    "geometry_rejections",
    "wire_reach_rejections",
    "metropolis_rejections",
    "implementation_proposals",
    "relay_proposals",
    "implementation_moves_accepted",
    "relay_moves_accepted",
    "swap_attempts",
    "swaps_accepted",
    "topology_rebuild_attempts",
    "topology_rebuild_successes",
)


@contextmanager
def _tracking(enabled: bool) -> Iterator[None]:
    original = incremental._TRACK_EXACT_ACCEPTED_MOVES
    incremental._TRACK_EXACT_ACCEPTED_MOVES = enabled
    try:
        yield
    finally:
        incremental._TRACK_EXACT_ACCEPTED_MOVES = original


def _run(factory: CaseFactory, *, proposals: int, seed: int, tracking: bool):
    case = factory()
    options = PlacementOptions(
        anchor_io=False,
        reserve_corridors=False,
        iterations=proposals,
        random_seed=seed,
        restarts=1,
    )
    started = perf_counter()
    with _tracking(tracking):
        observed = optimize_physical_layout_observed(case.problem, options=options)
    elapsed = perf_counter() - started
    validate_physical_layout(replace(case.problem, layout=observed.optimization.layout))
    return observed, elapsed


def _assert_same_trajectory(baseline: Any, candidate: Any) -> None:
    for field in _TRAJECTORY_FIELDS:
        left = getattr(baseline.stats, field)
        right = getattr(candidate.stats, field)
        if left != right:
            raise AssertionError(f"trajectory counter {field} changed: {left} != {right}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposals", type=int, default=4096)
    parser.add_argument("--seed", type=int, default=0, help="first random seed")
    parser.add_argument("--seeds", type=int, default=3)
    args = parser.parse_args()
    if args.proposals <= 0:
        parser.error("--proposals must be positive")
    if args.seeds <= 0:
        parser.error("--seeds must be positive")

    cases: tuple[tuple[str, CaseFactory], ...] = (
        ("relay-forest", _relay_forest_case),
        ("shared-bus", _shared_bus_case),
        ("clustered-sparse-cut", _clustered_sparse_cut_case),
        ("narrow-corridor", _narrow_corridor_case),
        ("perimeter-anchor", _perimeter_anchor_case),
        ("fixed-endpoint-span", _fixed_endpoint_span_case),
    )
    totals = {"better": 0, "equal": 0, "worse": 0}
    ratios: list[float] = []
    for name, factory in cases:
        case_ratios: list[float] = []
        case_counts = {"better": 0, "equal": 0, "worse": 0}
        for seed in range(args.seed, args.seed + args.seeds):
            baseline, baseline_time = _run(
                factory,
                proposals=args.proposals,
                seed=seed,
                tracking=False,
            )
            candidate, candidate_time = _run(
                factory,
                proposals=args.proposals,
                seed=seed,
                tracking=True,
            )
            _assert_same_trajectory(baseline, candidate)
            before = baseline.optimization.after.objective
            after = candidate.optimization.after.objective
            if after < before:
                outcome = "better"
            elif after > before:
                outcome = "worse"
            else:
                outcome = "equal"
            if outcome == "worse":
                raise AssertionError(
                    f"incremental exact tracking lost a baseline state for {name} seed {seed}: "
                    f"{before} -> {after}"
                )
            ratio = candidate_time / baseline_time if baseline_time else 1.0
            case_ratios.append(ratio)
            ratios.append(ratio)
            case_counts[outcome] += 1
            totals[outcome] += 1
            print(
                f"{name} seed={seed}: baseline={before}, tracked={after}, outcome={outcome}, "
                f"accepted={candidate.stats.accepted_moves}, "
                f"runtime={baseline_time:.3f}s->{candidate_time:.3f}s"
            )
        print(
            f"SUMMARY {name}: better/equal/worse={case_counts['better']}/"
            f"{case_counts['equal']}/{case_counts['worse']}, "
            f"median-runtime-ratio={median(case_ratios):.3f}"
        )
    print(
        f"OVERALL better/equal/worse={totals['better']}/{totals['equal']}/{totals['worse']}, "
        f"median-runtime-ratio={median(ratios):.3f}"
    )


if __name__ == "__main__":
    main()
