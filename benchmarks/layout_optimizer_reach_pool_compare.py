"""Compare the baseline annealer with bounded reach-immobile proposal filtering."""

from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
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
from benchmarks.layout_optimizer_topology_corpus import (
    _clustered_sparse_cut_case,
    _near_optimal_packed_case,
    _red_green_mesh_case,
)
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis.layout_observability import optimize_physical_layout_observed
from factorio_circuit.synthesis.layout_optimizer import validate_physical_layout
from factorio_circuit.synthesis.placement import PlacementOptions

CaseFactory = Callable[[], Any]


def _bounded_reach_feasible_alternative(
    state: Any,
    topology: Any,
    object_id: int,
    grid: Any,
) -> bool:
    """Decision-equivalent reach-domain test that scans only the local coordinate rectangle."""

    incident = topology.incident_wires.get(object_id, ())
    if not incident:
        return True
    current = state.object_position(object_id)
    if object_id in state.relay_positions:
        x_positions = grid.unit_x_positions
    else:
        entity = state.circuit.entity_by_id(object_id)
        x_positions = (
            grid.unit_x_positions
            if isinstance(entity, incremental.ConstantCombinator)
            else grid.x_positions
        )
    y_positions = grid.y_positions

    neighbors = []
    for wire in incident:
        remote, _connector = incremental._remote_endpoint(wire, object_id)
        if remote != object_id:
            neighbors.append(state.object_position(remote))
    if not neighbors:
        return True

    safe_span = state.safe_span
    left = max(position[0] - safe_span for position in neighbors)
    right = min(position[0] + safe_span for position in neighbors)
    top = max(position[1] - safe_span for position in neighbors)
    bottom = min(position[1] + safe_span for position in neighbors)
    if left > right + incremental._EPSILON or top > bottom + incremental._EPSILON:
        return False

    x_start = bisect_left(x_positions, left - incremental._EPSILON)
    x_end = bisect_right(x_positions, right + incremental._EPSILON)
    y_start = bisect_left(y_positions, top - incremental._EPSILON)
    y_end = bisect_right(y_positions, bottom + incremental._EPSILON)
    for x in x_positions[x_start:x_end]:
        for y in y_positions[y_start:y_end]:
            candidate = (x, y)
            if candidate == current:
                continue
            if all(
                incremental._distance(candidate, neighbor)
                <= safe_span + incremental._EPSILON
                for neighbor in neighbors
            ):
                return True
    return False


@contextmanager
def _filtering(enabled: bool) -> Iterator[None]:
    original_filter = incremental._FILTER_REACH_IMMOBILE_PROPOSALS
    original_predicate = incremental._has_reach_feasible_alternative
    incremental._FILTER_REACH_IMMOBILE_PROPOSALS = enabled
    if enabled:
        incremental._has_reach_feasible_alternative = _bounded_reach_feasible_alternative
    try:
        yield
    finally:
        incremental._has_reach_feasible_alternative = original_predicate
        incremental._FILTER_REACH_IMMOBILE_PROPOSALS = original_filter


def _run(factory: CaseFactory, *, proposals: int, seed: int, filtering: bool):
    case = factory()
    options = PlacementOptions(
        anchor_io=False,
        reserve_corridors=False,
        iterations=proposals,
        random_seed=seed,
        restarts=1,
    )
    started = perf_counter()
    with _filtering(filtering):
        observed = optimize_physical_layout_observed(case.problem, options=options)
    elapsed = perf_counter() - started
    validate_physical_layout(replace(case.problem, layout=observed.optimization.layout))
    return observed, elapsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposals", type=int, default=4096)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--seeds", type=int, default=8)
    args = parser.parse_args()
    if args.proposals <= 0:
        parser.error("--proposals must be positive")
    if args.seeds <= 0:
        parser.error("--seeds must be positive")

    cases: tuple[tuple[str, CaseFactory], ...] = (
        ("relay-forest", _relay_forest_case),
        ("shared-bus", _shared_bus_case),
        ("clustered-sparse-cut", _clustered_sparse_cut_case),
        ("red-green-mesh", _red_green_mesh_case),
        ("near-optimal-packed", _near_optimal_packed_case),
        ("narrow-corridor", _narrow_corridor_case),
        ("perimeter-anchor", _perimeter_anchor_case),
        ("fixed-endpoint-span", _fixed_endpoint_span_case),
    )
    totals = {"better": 0, "equal": 0, "worse": 0}
    runtime_ratios: list[float] = []
    any_worse = False

    for name, factory in cases:
        case_counts = {"better": 0, "equal": 0, "worse": 0}
        case_ratios: list[float] = []
        attempted_delta: list[int] = []
        reach_delta: list[int] = []
        accepted_delta: list[int] = []
        for seed in range(args.seed, args.seed + args.seeds):
            baseline, baseline_time = _run(
                factory,
                proposals=args.proposals,
                seed=seed,
                filtering=False,
            )
            candidate, candidate_time = _run(
                factory,
                proposals=args.proposals,
                seed=seed,
                filtering=True,
            )
            before = baseline.optimization.after.objective
            after = candidate.optimization.after.objective
            if after < before:
                outcome = "better"
            elif after > before:
                outcome = "worse"
                any_worse = True
            else:
                outcome = "equal"

            ratio = candidate_time / baseline_time if baseline_time else 1.0
            case_ratios.append(ratio)
            runtime_ratios.append(ratio)
            case_counts[outcome] += 1
            totals[outcome] += 1
            attempted_change = (
                candidate.stats.proposals_attempted - baseline.stats.proposals_attempted
            )
            reach_change = (
                candidate.stats.wire_reach_rejections
                - baseline.stats.wire_reach_rejections
            )
            accepted_change = candidate.stats.accepted_moves - baseline.stats.accepted_moves
            attempted_delta.append(attempted_change)
            reach_delta.append(reach_change)
            accepted_delta.append(accepted_change)
            print(
                f"{name} seed={seed}: baseline={before}, filtered={after}, outcome={outcome}, "
                f"attempted={baseline.stats.proposals_attempted}->"
                f"{candidate.stats.proposals_attempted}, "
                f"accepted={baseline.stats.accepted_moves}->{candidate.stats.accepted_moves}, "
                f"reach={baseline.stats.wire_reach_rejections}->"
                f"{candidate.stats.wire_reach_rejections}, "
                f"runtime={baseline_time:.3f}s->{candidate_time:.3f}s"
            )

        print(
            f"SUMMARY {name}: better/equal/worse={case_counts['better']}/"
            f"{case_counts['equal']}/{case_counts['worse']}, "
            f"median-delta(attempted={median(attempted_delta):+.0f}, "
            f"reach={median(reach_delta):+.0f}, accepted={median(accepted_delta):+.0f}), "
            f"median-runtime-ratio={median(case_ratios):.3f}"
        )

    print(
        f"OVERALL better/equal/worse={totals['better']}/{totals['equal']}/"
        f"{totals['worse']}, median-runtime-ratio={median(runtime_ratios):.3f}"
    )
    if any_worse:
        raise SystemExit("reach-mobile filtering lost at least one baseline objective")


if __name__ == "__main__":
    main()
