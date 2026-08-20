"""Solve the experimental temporal Snake plan, synthesize it, and emit a Factorio blueprint.

This runner is intentionally separate from :mod:`benchmarks.snake.generate`. The canonical generator
continues to exercise the accepted ordinary ALAP lowering path until the temporal delay-bus plan has
been validated in Factorio.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from benchmarks.snake.generate import _TerminalProgress
from benchmarks.snake.random_model import FOOD_CANDIDATE_ORACLE, build_random_snake_circuit
from factorio_circuit import RandomSignalOracleProvider, SamplingPolicy, lower_to_abstract_physical
from factorio_circuit.analysis import (
    build_temporal_hypergraph,
    census_abstract_physical,
    format_abstract_physical_census,
    format_temporal_optimization,
    optimize_temporal_hypergraph,
)
from factorio_circuit.blueprint.layout_encode import (
    encode_layout_blueprint_string,
    layout_to_blueprint_json,
)
from factorio_circuit.blueprint.routing import DEFAULT_SAFE_WIRE_SPAN
from factorio_circuit.ir.physical import WireColor
from factorio_circuit.lowering.temporal_plan import lower_normalized_vectors_with_temporal_plan
from factorio_circuit.oracles import (
    materialize_oracle_providers,
    validate_oracle_provider_bindings,
)
from factorio_circuit.synthesis.open_vector import synthesize_vector_layout
from factorio_circuit.synthesis.safe_crossbar import safe_crossbar_options
from factorio_circuit.synthesis.safe_folded_crossbar import safe_folded_crossbar_options
from factorio_circuit.target.factorio.signals import DEFAULT_VIRTUAL_SIGNAL_POOL


def _marker_wire_color(layout: object, marker_entity: int) -> WireColor:
    wires = getattr(layout, "wires")
    colors = {
        wire.color
        for wire in wires
        if wire.source_entity == marker_entity or wire.target_entity == marker_entity
    }
    if len(colors) != 1:
        rendered = ", ".join(sorted(color.value for color in colors)) or "none"
        raise ValueError(
            f"expected exactly one synthesized wire color at marker {marker_entity}; found {rendered}"
        )
    return next(iter(colors))


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
    parser.add_argument("--census", action="store_true")
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
    graph = build_temporal_hypergraph(
        lowered.optimized_ir,
        lowered.state_timing,
        sampling_policy=sampling_policy,
    )
    optimization = optimize_temporal_hypergraph(
        graph,
        bus_capacity=args.bus_capacity,
        max_buses=args.max_buses,
        time_limit_seconds=args.time_limit,
        workers=args.workers,
    )
    print(format_temporal_optimization(optimization), file=sys.stderr)

    planned = lower_normalized_vectors_with_temporal_plan(
        lowered.optimized_ir,
        state_timing=lowered.state_timing,
        sampling_policy=sampling_policy,
        graph=graph,
        optimization=optimization,
    )
    materialize_oracle_providers(
        lowered.optimized_ir,
        planned,
        validate_oracle_provider_bindings(lowered.optimized_ir, providers),
    )
    census = census_abstract_physical(planned)
    if args.census:
        print(format_abstract_physical_census(census), file=sys.stderr)

    progress = None if args.no_progress else _TerminalProgress()
    placement = safe_crossbar_options() if args.linear_safe_layout else safe_folded_crossbar_options()
    try:
        layout = synthesize_vector_layout(
            planned,
            safe_wire_span=DEFAULT_SAFE_WIRE_SPAN,
            placement=placement,
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
        raise ValueError("temporal Snake blueprint contains no Random Input selector")

    movement = next(port for port in layout.circuit.inputs if port.name == "movement")
    reset = next(port for port in layout.circuit.inputs if port.name == "reset")
    framebuffer = next(port for port in layout.circuit.outputs if port.name == "framebuffer")
    movement_color = _marker_wire_color(layout, movement.marker_entity)
    reset_color = _marker_wire_color(layout, reset.marker_entity)
    framebuffer_color = _marker_wire_color(layout, framebuffer.marker_entity)
    if reset.signal is None:
        raise ValueError("scalar reset port unexpectedly has no concrete signal")

    print(
        "temporal snake: "
        f"combinators={layout.circuit.combinator_count}, relays={len(layout.relays)}, "
        f"state_period={lowered.state_timing.uniform_period}, objective={optimization.objective_delays}, "
        f"best_bound={optimization.best_bound}, abstract_max_lanes={census.max_signals_per_net}",
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
        f"reset={layout.positions[reset.marker_entity]}, movement={layout.positions[movement.marker_entity]}, "
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
