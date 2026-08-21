"""Generate Snake with observation-aware residual exact transport and isolated shared buses.

This runner is intentionally separate from the accepted canonical Snake generator.  It freezes the
state cone to the already-used production ALAP schedule, classifies every use as reuse, fresh
observation, or residual exact transport, optimizes only those residual exact transports, and lowers
the resulting private chains/shared scalar buses through the isolated abstract-lane implementation.

OR-Tools is only required when the fixed placement contains at least two long scalar bus candidates.
A typical invocation is::

    uv run --with 'ortools>=9.14,<10' python -m benchmarks.snake.generate_transport --census-only
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from benchmarks.snake.generate import _TerminalProgress
from benchmarks.snake.generate_temporal import _marker_wire_color, _pin_graph_to_schedule
from benchmarks.snake.random_model import FOOD_CANDIDATE_ORACLE, build_random_snake_circuit
from factorio_circuit import RandomSignalOracleProvider, SamplingPolicy, lower_to_abstract_physical
from factorio_circuit.analysis import (
    analyze_temporal_alignment,
    build_temporal_hypergraph,
    census_abstract_physical,
    format_abstract_physical_census,
    format_transport_optimization,
    optimize_exact_transports,
)
from factorio_circuit.blueprint.layout_encode import (
    encode_layout_blueprint_string,
    layout_to_blueprint_json,
)
from factorio_circuit.blueprint.routing import DEFAULT_SAFE_WIRE_SPAN
from factorio_circuit.lowering.alap import build_alap_schedule
from factorio_circuit.lowering.transport_plan import (
    lower_normalized_vectors_with_observation_aware_transport,
)
from factorio_circuit.oracles import (
    materialize_oracle_providers,
    validate_oracle_provider_bindings,
)
from factorio_circuit.synthesis.open_vector import synthesize_vector_layout
from factorio_circuit.synthesis.safe_crossbar import safe_crossbar_options
from factorio_circuit.synthesis.safe_folded_crossbar import safe_folded_crossbar_options
from factorio_circuit.target.factorio.signals import DEFAULT_VIRTUAL_SIGNAL_POOL


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write blueprint string to this file")
    parser.add_argument("--time-limit", type=float, default=30.0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--bus-capacity", type=int, default=len(DEFAULT_VIRTUAL_SIGNAL_POOL))
    parser.add_argument("--max-buses", type=int)
    parser.add_argument(
        "--linear-safe-layout",
        action="store_true",
        help="use the one-row safe-crossbar instead of the default folded safe layout",
    )
    parser.add_argument(
        "--census-only",
        action="store_true",
        help="stop after abstract physical lowering and report the structural census",
    )
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args()

    sampling_policy = SamplingPolicy.ALAP
    providers = {FOOD_CANDIDATE_ORACLE: RandomSignalOracleProvider(update_interval=1)}
    circuit = build_random_snake_circuit()
    physical_module = circuit._build_for_physical()

    lowered = lower_to_abstract_physical(
        physical_module,
        optimize=False,
        oracle_providers=providers,
        sampling_policy=sampling_policy,
    )
    accepted_census = census_abstract_physical(lowered.abstract_physical)
    graph = build_temporal_hypergraph(
        lowered.optimized_ir,
        lowered.state_timing,
        sampling_policy=sampling_policy,
    )
    accepted_schedule = build_alap_schedule(lowered.optimized_ir, lowered.state_timing)
    pinned_graph = _pin_graph_to_schedule(graph, accepted_schedule)
    placement = pinned_graph.alap_placement()
    alignment = analyze_temporal_alignment(pinned_graph, placement)

    use_kinds = Counter(item.kind.value for item in alignment.uses)
    print(
        "observation-aware Snake transport: fixed production ALAP placement; "
        + ", ".join(f"{kind}={count}" for kind, count in sorted(use_kinds.items())),
        file=sys.stderr,
    )
    print(
        "residual exact lifetimes: "
        f"total={len(alignment.transports)}, "
        f"scalar_bus_candidates={sum(item.scalar_bus_candidate for item in alignment.transports)}",
        file=sys.stderr,
    )

    optimization = optimize_exact_transports(
        alignment,
        bus_capacity=args.bus_capacity,
        max_buses=args.max_buses,
        time_limit_seconds=args.time_limit,
        workers=args.workers,
    )
    print(format_transport_optimization(optimization), file=sys.stderr)

    planned = lower_normalized_vectors_with_observation_aware_transport(
        lowered.optimized_ir,
        state_timing=lowered.state_timing,
        sampling_policy=sampling_policy,
        graph=pinned_graph,
        placement=placement,
        optimization=optimization,
    )
    materialize_oracle_providers(
        lowered.optimized_ir,
        planned,
        validate_oracle_provider_bindings(lowered.optimized_ir, providers),
    )
    planned_census = census_abstract_physical(planned)
    print(format_abstract_physical_census(planned_census), file=sys.stderr)
    print(
        "accepted ALAP vs observation-aware transport: "
        f"accepted={accepted_census.implementation_entities}; "
        f"planned={planned_census.implementation_entities}; "
        f"delta={planned_census.implementation_entities - accepted_census.implementation_entities:+d}",
        file=sys.stderr,
    )

    if args.census_only:
        return

    progress = None if args.no_progress else _TerminalProgress()
    layout_options = (
        safe_crossbar_options() if args.linear_safe_layout else safe_folded_crossbar_options()
    )
    try:
        layout = synthesize_vector_layout(
            planned,
            safe_wire_span=DEFAULT_SAFE_WIRE_SPAN,
            placement=layout_options,
            progress=progress,
        )
    finally:
        if progress is not None:
            progress.close()

    blueprint_json = layout_to_blueprint_json(layout)
    blueprint_string = encode_layout_blueprint_string(layout)
    entities = blueprint_json["blueprint"]["entities"]  # type: ignore[index]
    selectors = [
        entity
        for entity in entities
        if isinstance(entity, dict) and entity.get("name") == "selector-combinator"
    ]
    if not any(
        isinstance(entity.get("control_behavior"), dict)
        and entity["control_behavior"].get("operation") == "random"
        for entity in selectors
    ):
        raise ValueError("observation-aware Snake blueprint contains no Random Input selector")

    movement = next(port for port in layout.circuit.inputs if port.name == "movement")
    reset = next(port for port in layout.circuit.inputs if port.name == "reset")
    framebuffer = next(port for port in layout.circuit.outputs if port.name == "framebuffer")
    movement_color = _marker_wire_color(layout, movement.marker_entity)
    reset_color = _marker_wire_color(layout, reset.marker_entity)
    framebuffer_color = _marker_wire_color(layout, framebuffer.marker_entity)
    if reset.signal is None:
        raise ValueError("scalar reset port unexpectedly has no concrete signal")

    print(
        "observation-aware snake: "
        f"combinators={layout.circuit.combinator_count}, relays={len(layout.relays)}, "
        f"state_period={lowered.state_timing.uniform_period}, "
        f"transport_objective={optimization.objective_combinators}, "
        f"best_bound={optimization.best_bound}, buses={len(optimization.buses)}, "
        f"abstract_max_lanes={planned_census.max_signals_per_net}",
        file=sys.stderr,
    )
    print(
        "wire movement detector -> INPUT movement with "
        f"{movement_color.value.upper()}; pulse INPUT reset [{reset.signal.name}] nonzero with "
        f"{reset_color.value.upper()}; OUTPUT framebuffer -> display with "
        f"{framebuffer_color.value.upper()}",
        file=sys.stderr,
    )
    print(
        "front-panel marker positions (relative blueprint coordinates): "
        f"reset={layout.positions[reset.marker_entity]}, "
        f"movement={layout.positions[movement.marker_entity]}, "
        f"framebuffer={layout.positions[framebuffer.marker_entity]}",
        file=sys.stderr,
    )

    if args.output is None:
        print(blueprint_string)
    else:
        args.output.write_text(blueprint_string + "\n", encoding="utf-8")
        print(f"wrote blueprint to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
