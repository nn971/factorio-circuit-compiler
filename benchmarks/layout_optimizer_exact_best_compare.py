"""Compare epoch-only and accepted-move exact-best tracking on identical layout seeds."""

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
def _mid_epoch_tracking_disabled() -> Iterator[None]:
    original = incremental._accepted_move_exact_score

    def disabled(*_args: Any, **_kwargs: Any) -> tuple[int, float, float]:
        return (10**18, float("inf"), float("inf"))

    incremental._accepted_move_exact_score = disabled
    try:
        yield
    finally:
        incremental._accepted_move_exact_score = original


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
    if tracking:
        observed = optimize_physical_layout_observed(case.problem, options=options)
    else:
        with _mid_epoch_tracking_disabled():
            observed = optimize_physical_layout_observed(case.problem, options=options)
    elapsed = perf_counter() - started
    validate_physical_layout(replace(case.problem, layout=observed.optimization.layout))
    return observed, elapsed


def _assert_same_trajectory(baseline: Any, tracked: Any) -> None:
    for field in _TRAJECTORY_FIELDS:
        left = getattr(baseline.stats, field)
        right = getattr(tracked.stats, field)
        if left != right:
            raise AssertionError(f"trajectory counter {field} changed: {left} != {right}")


def _run_case(
    name: str,
    factory: CaseFactory,
    *,
    proposals: int,
    first_seed: int,
    seeds: int,
) -> tuple[int, int, int]:
    outcomes = {"better": 0, "equal": 0, "worse": 0}
    relay_deltas: list[int] = []
    area_deltas: list[float] = []
    wire_deltas: list[float] = []
    runtime_ratios: list[float] = []
    accepted_moves: list[int] = []

    for seed in range(first_seed, first_seed + seeds):
        baseline, baseline_time = _run(factory, proposals=proposals, seed=seed, tracking=False)
        tracked, tracked_time = _run(factory, proposals=proposals, seed=seed, tracking=True)
        _assert_same_trajectory(baseline, tracked)
        baseline_metrics = baseline.optimization.after
        tracked_metrics = tracked.optimization.after
        if tracked_metrics.objective < baseline_metrics.objective:
            outcome = "better"
        elif tracked_metrics.objective > baseline_metrics.objective:
            outcome = "worse"
        else:
            outcome = "equal"
        outcomes[outcome] += 1
        if outcome == "worse":
            raise AssertionError(
                f"exact-best tracking lost a baseline state for {name} seed {seed}: "
                f"{baseline_metrics.objective} -> {tracked_metrics.objective}"
            )
        relay_deltas.append(tracked_metrics.relay_count - baseline_metrics.relay_count)
        area_deltas.append(tracked_metrics.occupied_area - baseline_metrics.occupied_area)
        wire_deltas.append(tracked_metrics.wire_length - baseline_metrics.wire_length)
        runtime_ratios.append(tracked_time / baseline_time if baseline_time else 1.0)
        accepted_moves.append(tracked.stats.accepted_moves)
        print(
            f"{name} seed={seed}: baseline={baseline_metrics.objective}, "
            f"tracked={tracked_metrics.objective}, outcome={outcome}, "
            f"accepted={tracked.stats.accepted_moves}, "
            f"runtime={baseline_time:.3f}s->{tracked_time:.3f}s"
        )

    outcome_text = "/".join(str(outcomes[key]) for key in ("better", "equal", "worse"))
    print(
        f"SUMMARY {name}: better/equal/worse={outcome_text}, "
        f"median-delta(relays={median(relay_deltas):+.1f}, area={median(area_deltas):+.2f}, "
        f"wire={median(wire_deltas):+.3f}), "
        f"median-runtime-ratio={median(runtime_ratios):.3f}, "
        f"median-accepted={median(accepted_moves):.1f}"
    )
    return outcomes["better"], outcomes["equal"], outcomes["worse"]


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
    totals = [0, 0, 0]
    for name, factory in cases:
        result = _run_case(
            name,
            factory,
            proposals=args.proposals,
            first_seed=args.seed,
            seeds=args.seeds,
        )
        totals = [left + right for left, right in zip(totals, result, strict=True)]
    print(f"OVERALL better/equal/worse={totals[0]}/{totals[1]}/{totals[2]}")


if __name__ == "__main__":
    main()
