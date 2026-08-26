"""Compare fixed-schedule and adaptive retopology on identical layout seeds."""

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
    _shared_bus_case,
)
from benchmarks.layout_optimizer_topology_corpus import _red_green_mesh_case
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis.layout_observability import optimize_physical_layout_observed
from factorio_circuit.synthesis.layout_optimizer import validate_physical_layout
from factorio_circuit.synthesis.placement import PlacementOptions

CaseFactory = Callable[[], Any]


@contextmanager
def _fixed_schedule_only() -> Iterator[None]:
    """Temporarily disable only adaptive rebuild reasons for a measurement baseline."""

    original = incremental._anneal_rebuild_reason

    def fixed_schedule_reason(**kwargs: Any) -> str | None:
        if kwargs["epoch_end"] >= kwargs["iterations"]:
            return None
        if kwargs["epoch_end"] in kwargs["scheduled_rebuilds"]:
            return "scheduled"
        return None

    incremental._anneal_rebuild_reason = fixed_schedule_reason
    try:
        yield
    finally:
        incremental._anneal_rebuild_reason = original


def _run(factory: CaseFactory, *, proposals: int, seed: int, adaptive: bool):
    case = factory()
    options = PlacementOptions(
        anchor_io=False,
        reserve_corridors=False,
        iterations=proposals,
        random_seed=seed,
        restarts=1,
    )
    started = perf_counter()
    if adaptive:
        observed = optimize_physical_layout_observed(case.problem, options=options)
    else:
        with _fixed_schedule_only():
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
    objective_outcomes = {"better": 0, "equal": 0, "worse": 0}
    relay_deltas: list[int] = []
    area_deltas: list[float] = []
    wire_deltas: list[float] = []
    queue_pop_deltas: list[int] = []
    runtime_ratios: list[float] = []
    adaptive_attempts = 0
    adaptive_successes = 0

    for seed in range(first_seed, first_seed + seeds):
        fixed, fixed_time = _run(factory, proposals=proposals, seed=seed, adaptive=False)
        adaptive, adaptive_time = _run(factory, proposals=proposals, seed=seed, adaptive=True)
        fixed_metrics = fixed.optimization.after
        adaptive_metrics = adaptive.optimization.after
        if adaptive_metrics.objective < fixed_metrics.objective:
            outcome = "better"
        elif adaptive_metrics.objective > fixed_metrics.objective:
            outcome = "worse"
        else:
            outcome = "equal"
        objective_outcomes[outcome] += 1
        relay_deltas.append(adaptive_metrics.relay_count - fixed_metrics.relay_count)
        area_deltas.append(adaptive_metrics.occupied_area - fixed_metrics.occupied_area)
        wire_deltas.append(adaptive_metrics.wire_length - fixed_metrics.wire_length)
        queue_pop_deltas.append(
            adaptive.stats.routing_queue_pops - fixed.stats.routing_queue_pops
        )
        runtime_ratios.append(adaptive_time / fixed_time if fixed_time else 1.0)
        adaptive_attempts += adaptive.stats.adaptive_topology_rebuild_attempts
        adaptive_successes += adaptive.stats.adaptive_topology_rebuild_successes
        print(
            f"{name} seed={seed}: fixed={fixed_metrics.objective}, "
            f"adaptive={adaptive_metrics.objective}, outcome={outcome}, "
            f"adaptive-rebuilds={adaptive.stats.adaptive_topology_rebuild_successes}/"
            f"{adaptive.stats.adaptive_topology_rebuild_attempts}, "
            f"queue-pops={fixed.stats.routing_queue_pops}->{adaptive.stats.routing_queue_pops}, "
            f"runtime={fixed_time:.3f}s->{adaptive_time:.3f}s"
        )

    print(
        f"SUMMARY {name}: better/equal/worse="
        f"{objective_outcomes['better']}/{objective_outcomes['equal']}/{objective_outcomes['worse']}, "
        f"median-delta(relays={median(relay_deltas):+.1f}, area={median(area_deltas):+.2f}, "
        f"wire={median(wire_deltas):+.3f}, queue-pops={median(queue_pop_deltas):+.1f}), "
        f"median-runtime-ratio={median(runtime_ratios):.3f}, "
        f"adaptive-rebuilds={adaptive_successes}/{adaptive_attempts}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposals", type=int, default=4096)
    parser.add_argument("--seed", type=int, default=0, help="first seed")
    parser.add_argument("--seeds", type=int, default=3)
    args = parser.parse_args()
    if args.proposals <= 0:
        parser.error("--proposals must be positive")
    if args.seeds <= 0:
        parser.error("--seeds must be positive")

    cases: tuple[tuple[str, CaseFactory], ...] = (
        ("shared-bus", _shared_bus_case),
        ("fixed-endpoint-span", _fixed_endpoint_span_case),
        ("narrow-corridor", _narrow_corridor_case),
        ("red-green-mesh", _red_green_mesh_case),
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
