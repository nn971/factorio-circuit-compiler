"""Application-level Milestone C acceptance for the generic physical layout optimizer.

This benchmark intentionally starts from the failproof safe-folded Snake layout, then applies the
same public ``optimize_physical_layout`` API used by non-Snake callers. It measures the hard
application criteria that the structural corpus cannot establish: footprint occupancy, relay burden,
known-redundant relays, and bounded wall-clock convergence at Snake scale.

The benchmark is opt-in because constructing and routing the complete Snake blueprint is expensive.
By default it is a *strict* acceptance command and exits non-zero until every hard criterion passes.
Use ``--measure-only`` while developing new general-purpose optimization strategies.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from time import monotonic

from benchmarks.snake.generate import _safe_folded_seed_problem
from benchmarks.snake.random_model import (
    DEFAULT_LOGICAL_STEPS_PER_MOVE,
    FOOD_CANDIDATE_ORACLE,
    build_random_snake_circuit,
)
from factorio_circuit import RandomSignalOracleProvider, SamplingPolicy
from factorio_circuit.blueprint.layout_encode import encode_layout_blueprint_string
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis import layout_optimizer
from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.layout import Layout
from factorio_circuit.synthesis.layout_optimizer import (
    LayoutOptimizationProblem,
    optimize_physical_layout,
    physical_layout_metrics,
    validate_physical_layout,
)
from factorio_circuit.synthesis.placement import PlacementOptions
from factorio_circuit.synthesis.safe_folded_crossbar import safe_folded_crossbar_options

DEFAULT_MIN_OCCUPANCY = 0.80
DEFAULT_MAX_RUNTIME_SECONDS = 600.0


@dataclass(frozen=True, slots=True)
class ApplicationLayoutMetrics:
    implementation_combinators: int
    relay_combinators: int
    entity_footprint_area: float
    bounding_box_area: float
    occupancy: float
    implementation_per_relay: float | None
    wire_length: float
    known_redundant_relays: int | None


def _entity_footprint_area(layout: Layout) -> float:
    relay_ids = {relay.entity_id for relay in layout.relays}
    entities = {entity.id: entity for entity in layout.circuit.entities}
    total = 0.0
    for object_id in layout.positions:
        if object_id in relay_ids:
            total += 1.0
            continue
        half_x, half_y = base_placement._entity_half_extent(entities[object_id])
        total += 4.0 * half_x * half_y
    return total


def _known_redundant_relays(problem: LayoutOptimizationProblem) -> int:
    """Count relays removed by the general topology-preserving simplifier to fixed point."""

    embedding = layout_optimizer._validated_embedding(problem)
    state = embedding.state
    topology = embedding.topology
    initial = len(state.relay_positions)
    while True:
        before = len(state.relay_positions)
        topology = incremental._simplify_feasible_topology(state, topology)
        if len(state.relay_positions) == before:
            break
    return initial - len(state.relay_positions)


def application_layout_metrics(
    layout: Layout,
    *,
    problem: LayoutOptimizationProblem,
    check_redundancy: bool = True,
) -> ApplicationLayoutMetrics:
    public = physical_layout_metrics(layout)
    footprint = _entity_footprint_area(layout)
    occupancy = 1.0 if public.occupied_area == 0.0 else footprint / public.occupied_area
    ratio = None if public.relay_count == 0 else public.implementation_entities / public.relay_count
    return ApplicationLayoutMetrics(
        implementation_combinators=public.implementation_entities,
        relay_combinators=public.relay_count,
        entity_footprint_area=footprint,
        bounding_box_area=public.occupied_area,
        occupancy=occupancy,
        implementation_per_relay=ratio,
        wire_length=public.wire_length,
        known_redundant_relays=(_known_redundant_relays(problem) if check_redundancy else None),
    )


def _build_failproof_seed() -> tuple[Layout, LayoutOptimizationProblem]:
    circuit = build_random_snake_circuit(logical_steps_per_move=DEFAULT_LOGICAL_STEPS_PER_MOVE)
    result = circuit.compile(
        optimize=False,
        placement=safe_folded_crossbar_options(),
        oracle_providers={
            FOOD_CANDIDATE_ORACLE: RandomSignalOracleProvider(update_interval=1),
        },
        sampling_policy=SamplingPolicy.ALAP,
        progress=None,
    )
    problem = _safe_folded_seed_problem(result.layout)
    validate_physical_layout(problem)
    return result.layout, problem


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposals", type=int, default=4096)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--min-occupancy", type=float, default=DEFAULT_MIN_OCCUPANCY)
    parser.add_argument("--max-runtime-seconds", type=float, default=DEFAULT_MAX_RUNTIME_SECONDS)
    parser.add_argument(
        "--measure-only",
        action="store_true",
        help="report metrics without failing the still-open Milestone C density gate",
    )
    parser.add_argument("--json-report", type=Path)
    parser.add_argument("--output-blueprint", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.proposals < 0:
        raise SystemExit("--proposals must be non-negative")
    if not 0.0 < args.min_occupancy <= 1.0:
        raise SystemExit("--min-occupancy must be in (0, 1]")
    if args.max_runtime_seconds <= 0.0:
        raise SystemExit("--max-runtime-seconds must be positive")

    seed_layout, problem = _build_failproof_seed()
    input_metrics = application_layout_metrics(
        seed_layout,
        problem=problem,
        check_redundancy=False,
    )

    started = monotonic()
    optimized = optimize_physical_layout(
        problem,
        options=PlacementOptions(
            anchor_io=False,
            reserve_corridors=False,
            iterations=args.proposals,
            random_seed=args.seed,
            restarts=1,
        ),
    )
    runtime_seconds = monotonic() - started
    output_problem = replace(problem, layout=optimized.layout)
    validate_physical_layout(output_problem)
    output_metrics = application_layout_metrics(optimized.layout, problem=output_problem)

    failures: list[str] = []
    if output_metrics.occupancy <= args.min_occupancy:
        failures.append(
            f"occupancy {output_metrics.occupancy:.6f} is not strictly greater than "
            f"{args.min_occupancy:.6f}"
        )
    if output_metrics.known_redundant_relays:
        failures.append(
            f"general relay simplifier can still remove "
            f"{output_metrics.known_redundant_relays} relays"
        )
    if runtime_seconds > args.max_runtime_seconds:
        failures.append(
            f"optimizer runtime {runtime_seconds:.3f}s exceeds {args.max_runtime_seconds:.3f}s"
        )

    report = {
        "benchmark": "snake-layout-acceptance",
        "seed_policy": "safe-folded-crossbar",
        "optimizer_seed": args.seed,
        "proposal_budget": args.proposals,
        "runtime_seconds": runtime_seconds,
        "minimum_occupancy": args.min_occupancy,
        "maximum_runtime_seconds": args.max_runtime_seconds,
        "input": asdict(input_metrics),
        "output": asdict(output_metrics),
        "diagnostics": list(optimized.diagnostics),
        "failures": failures,
        "accepted": not failures,
    }
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.json_report is not None:
        args.json_report.write_text(rendered + "\n", encoding="utf-8")
    if args.output_blueprint is not None:
        args.output_blueprint.write_text(
            encode_layout_blueprint_string(optimized.layout) + "\n",
            encoding="utf-8",
        )

    if failures and not args.measure_only:
        raise SystemExit("Milestone C Snake application acceptance failed")


if __name__ == "__main__":
    main()
