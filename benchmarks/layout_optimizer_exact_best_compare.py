"""Compare exact-best sampling strides on identical layout trajectories."""

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
def _exact_stride(stride: int) -> Iterator[None]:
    original = incremental._EXACT_BEST_ACCEPTED_STRIDE
    incremental._EXACT_BEST_ACCEPTED_STRIDE = stride
    try:
        yield
    finally:
        incremental._EXACT_BEST_ACCEPTED_STRIDE = original


def _run(factory: CaseFactory, *, proposals: int, seed: int, stride: int):
    case = factory()
    options = PlacementOptions(
        anchor_io=False,
        reserve_corridors=False,
        iterations=proposals,
        random_seed=seed,
        restarts=1,
    )
    started = perf_counter()
    with _exact_stride(stride):
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


def _run_case(
    name: str,
    factory: CaseFactory,
    *,
    proposals: int,
    first_seed: int,
    seeds: int,
    strides: tuple[int, ...],
) -> dict[int, tuple[int, int, int]]:
    baseline_stride = 10**9
    outcomes = {stride: {"better": 0, "equal": 0, "worse": 0} for stride in strides}
    runtime_ratios = {stride: [] for stride in strides}

    for seed in range(first_seed, first_seed + seeds):
        baseline, baseline_time = _run(
            factory, proposals=proposals, seed=seed, stride=baseline_stride
        )
        baseline_metrics = baseline.optimization.after
        for stride in strides:
            candidate, candidate_time = _run(factory, proposals=proposals, seed=seed, stride=stride)
            _assert_same_trajectory(baseline, candidate)
            candidate_metrics = candidate.optimization.after
            if candidate_metrics.objective < baseline_metrics.objective:
                outcome = "better"
            elif candidate_metrics.objective > baseline_metrics.objective:
                outcome = "worse"
            else:
                outcome = "equal"
            outcomes[stride][outcome] += 1
            if outcome == "worse":
                raise AssertionError(
                    f"exact-best stride {stride} lost a baseline state for {name} seed {seed}: "
                    f"{baseline_metrics.objective} -> {candidate_metrics.objective}"
                )
            runtime_ratios[stride].append(candidate_time / baseline_time if baseline_time else 1.0)
            print(
                f"{name} seed={seed} stride={stride}: baseline={baseline_metrics.objective}, "
                f"candidate={candidate_metrics.objective}, outcome={outcome}, "
                f"accepted={candidate.stats.accepted_moves}, "
                f"runtime={baseline_time:.3f}s->{candidate_time:.3f}s"
            )

    result: dict[int, tuple[int, int, int]] = {}
    for stride in strides:
        counts = outcomes[stride]
        triple = (counts["better"], counts["equal"], counts["worse"])
        result[stride] = triple
        print(
            f"SUMMARY {name} stride={stride}: better/equal/worse="
            f"{triple[0]}/{triple[1]}/{triple[2]}, "
            f"median-runtime-ratio={median(runtime_ratios[stride]):.3f}"
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposals", type=int, default=4096)
    parser.add_argument("--seed", type=int, default=0, help="first random seed")
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--strides", type=int, nargs="+", default=(8, 32, 128))
    args = parser.parse_args()
    if args.proposals <= 0:
        parser.error("--proposals must be positive")
    if args.seeds <= 0:
        parser.error("--seeds must be positive")
    if any(stride <= 0 for stride in args.strides):
        parser.error("--strides must be positive")
    strides = tuple(dict.fromkeys(args.strides))

    cases: tuple[tuple[str, CaseFactory], ...] = (
        ("relay-forest", _relay_forest_case),
        ("shared-bus", _shared_bus_case),
        ("clustered-sparse-cut", _clustered_sparse_cut_case),
        ("narrow-corridor", _narrow_corridor_case),
        ("perimeter-anchor", _perimeter_anchor_case),
        ("fixed-endpoint-span", _fixed_endpoint_span_case),
    )
    totals = {stride: [0, 0, 0] for stride in strides}
    for name, factory in cases:
        case_results = _run_case(
            name,
            factory,
            proposals=args.proposals,
            first_seed=args.seed,
            seeds=args.seeds,
            strides=strides,
        )
        for stride, counts in case_results.items():
            totals[stride] = [
                left + right for left, right in zip(totals[stride], counts, strict=True)
            ]
    for stride in strides:
        better, equal, worse = totals[stride]
        print(f"OVERALL stride={stride}: better/equal/worse={better}/{equal}/{worse}")


if __name__ == "__main__":
    main()
