"""Compare baseline and analytical implementation reach clipping on identical layout seeds."""

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


@contextmanager
def _clipping_disabled() -> Iterator[None]:
    """Temporarily restore the pre-experiment implementation reach rejection behavior."""

    original = incremental._reach_clipped_target

    def disabled(*args: Any, **kwargs: Any) -> None:
        return None

    incremental._reach_clipped_target = disabled
    try:
        yield
    finally:
        incremental._reach_clipped_target = original


def _run(factory: CaseFactory, *, proposals: int, seed: int, clipping: bool):
    case = factory()
    options = PlacementOptions(
        anchor_io=False,
        reserve_corridors=False,
        iterations=proposals,
        random_seed=seed,
        restarts=1,
    )
    started = perf_counter()
    if clipping:
        observed = optimize_physical_layout_observed(case.problem, options=options)
    else:
        with _clipping_disabled():
            observed = optimize_physical_layout_observed(case.problem, options=options)
    elapsed = perf_counter() - started
    validate_physical_layout(replace(case.problem, layout=observed.optimization.layout))
    return observed, elapsed


def _run_case(
    name: str,
    factory: CaseFactory,
    *,
    proposals: int,
    first_seed: int,
    seeds: int,
) -> None:
    outcomes = {"better": 0, "equal": 0, "worse": 0}
    relay_deltas: list[int] = []
    area_deltas: list[float] = []
    wire_deltas: list[float] = []
    reach_rejection_deltas: list[int] = []
    queue_pop_deltas: list[int] = []
    runtime_ratios: list[float] = []
    attempts = 0
    feasible = 0
    accepted = 0

    for seed in range(first_seed, first_seed + seeds):
        baseline, baseline_time = _run(factory, proposals=proposals, seed=seed, clipping=False)
        clipped, clipped_time = _run(factory, proposals=proposals, seed=seed, clipping=True)
        baseline_metrics = baseline.optimization.after
        clipped_metrics = clipped.optimization.after
        if clipped_metrics.objective < baseline_metrics.objective:
            outcome = "better"
        elif clipped_metrics.objective > baseline_metrics.objective:
            outcome = "worse"
        else:
            outcome = "equal"
        outcomes[outcome] += 1
        relay_deltas.append(clipped_metrics.relay_count - baseline_metrics.relay_count)
        area_deltas.append(clipped_metrics.occupied_area - baseline_metrics.occupied_area)
        wire_deltas.append(clipped_metrics.wire_length - baseline_metrics.wire_length)
        reach_rejection_deltas.append(
            clipped.stats.wire_reach_rejections - baseline.stats.wire_reach_rejections
        )
        queue_pop_deltas.append(
            clipped.stats.routing_queue_pops - baseline.stats.routing_queue_pops
        )
        runtime_ratios.append(clipped_time / baseline_time if baseline_time else 1.0)
        attempts += clipped.stats.reach_clip_attempts
        feasible += clipped.stats.reach_clip_feasible_targets
        accepted += clipped.stats.reach_clip_moves_accepted
        print(
            f"{name} seed={seed}: baseline={baseline_metrics.objective}, "
            f"clip={clipped_metrics.objective}, outcome={outcome}, "
            f"clip={clipped.stats.reach_clip_moves_accepted}/"
            f"{clipped.stats.reach_clip_feasible_targets}/"
            f"{clipped.stats.reach_clip_attempts}, "
            f"reach-reject={baseline.stats.wire_reach_rejections}->"
            f"{clipped.stats.wire_reach_rejections}, "
            f"queue-pops={baseline.stats.routing_queue_pops}->"
            f"{clipped.stats.routing_queue_pops}, "
            f"runtime={baseline_time:.3f}s->{clipped_time:.3f}s"
        )

    outcome_text = "/".join(str(outcomes[key]) for key in ("better", "equal", "worse"))
    print(
        f"SUMMARY {name}: better/equal/worse={outcome_text}, "
        f"median-delta(relays={median(relay_deltas):+.1f}, area={median(area_deltas):+.2f}, "
        f"wire={median(wire_deltas):+.3f}, reach-reject={median(reach_rejection_deltas):+.1f}, "
        f"queue-pops={median(queue_pop_deltas):+.1f}), "
        f"median-runtime-ratio={median(runtime_ratios):.3f}, "
        f"clip={accepted}/{feasible}/{attempts}"
    )


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
    for name, factory in cases:
        _run_case(
            name,
            factory,
            proposals=args.proposals,
            first_seed=args.seed,
            seeds=args.seeds,
        )


if __name__ == "__main__":
    main()
