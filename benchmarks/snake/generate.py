"""Compile and emit the heavyweight interactive Snake benchmark blueprint.

The default uses ``safe-folded-crossbar``: deterministic serpentine entity rows, row-local physical
bus tracks chosen after fold portals are known, and search-free vertical stitches. Public inputs and
outputs are clustered at the beginning of the first row. Food is proposed by a freely
placed selector combinator in Random Input mode and latched by deterministic Snake state.

External Level inputs/oracles use ALAP sampling by default for this benchmark, so their live circuit
network value is observed at the latest consumer phase instead of transported from phase zero.
``--sampling-policy beginning-of-step`` restores the historical snapshot baseline.

Progress is printed to stderr. The final importable blueprint string is printed to stdout unless
``--output`` names a file. Greedy, bounded annealing, row, and linear-safe layouts remain explicit
diagnostic/reference modes.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from math import ceil, floor
from pathlib import Path
from time import monotonic

from benchmarks.snake.random_model import (
    DEFAULT_LOGICAL_STEPS_PER_MOVE,
    FOOD_CANDIDATE_ORACLE,
    build_random_snake_circuit,
)
from factorio_circuit import (
    CompilationResult,
    CompileProgress,
    RandomSignalOracleProvider,
    SamplingPolicy,
)
from factorio_circuit.analysis import census_abstract_physical, format_abstract_physical_census
from factorio_circuit.blueprint.layout_encode import (
    encode_layout_blueprint_string,
    layout_to_blueprint_json,
)
from factorio_circuit.blueprint.routing import DEFAULT_SAFE_WIRE_SPAN
from factorio_circuit.devices._blueprint import decode_blueprint
from factorio_circuit.ir.physical import ConstantCombinator, WireColor
from factorio_circuit.synthesis.layout import Layout
from factorio_circuit.synthesis.layout_optimizer import (
    LayoutOptimizationProblem,
    LegalPlacementLattice,
    optimize_physical_layout,
)
from factorio_circuit.synthesis.placement import PlacementOptions
from factorio_circuit.synthesis.safe_crossbar import safe_crossbar_options
from factorio_circuit.synthesis.safe_folded_crossbar import safe_folded_crossbar_options


class _TerminalProgress:
    """Small dependency-free terminal renderer for structured compiler progress."""

    _BAR_WIDTH = 28

    def __init__(self) -> None:
        self._started = monotonic()
        self._inline = False

    def _elapsed(self) -> str:
        return f"{monotonic() - self._started:7.1f}s"

    def _finish_inline(self) -> None:
        if self._inline:
            print(file=sys.stderr)
            self._inline = False

    def __call__(self, event: CompileProgress) -> None:
        if event.completed is not None and event.total is not None and event.total > 0:
            fraction = event.fraction or 0.0
            filled = round(fraction * self._BAR_WIDTH)
            bar = "#" * filled + "-" * (self._BAR_WIDTH - filled)
            detail = f"  {event.detail}" if event.detail else ""
            line = (
                f"\r[{self._elapsed()}] {event.phase:18} [{bar}] "
                f"{event.completed}/{event.total}{detail}"
            )
            print(line, end="", file=sys.stderr, flush=True)
            self._inline = True
            return

        self._finish_inline()
        detail = f": {event.detail}" if event.detail else ""
        print(f"[{self._elapsed()}] {event.phase}{detail}", file=sys.stderr, flush=True)

    def close(self) -> None:
        self._finish_inline()


def _marker_wire_color(result: CompilationResult, marker_entity: int) -> WireColor:
    colors = {
        wire.color
        for wire in result.layout.wires
        if wire.source_entity == marker_entity or wire.target_entity == marker_entity
    }
    if len(colors) != 1:
        rendered = ", ".join(sorted(color.value for color in colors)) or "none"
        raise ValueError(
            "expected exactly one synthesized wire color at marker "
            f"{marker_entity}; found {rendered}"
        )
    return next(iter(colors))


def _validate_serialized_artifact(result: CompilationResult) -> None:
    """Check exact validated coordinates and connector wires after blueprint encoding."""

    decoded = decode_blueprint(result.blueprint_string)
    expected_blueprint = result.blueprint_json.get("blueprint")
    if decoded != expected_blueprint:
        raise ValueError("encoded Snake blueprint differs from its source blueprint JSON")
    entities = decoded.get("entities")
    if not isinstance(entities, list):
        raise ValueError("encoded Snake blueprint contains no entity list")
    serialized_positions = {
        int(entity["entity_number"]): (
            float(entity["position"]["x"]),
            float(entity["position"]["y"]),
        )
        for entity in entities
        if isinstance(entity, dict)
        and isinstance(entity.get("position"), dict)
        and "entity_number" in entity
    }
    if serialized_positions != result.layout.positions:
        raise ValueError("encoded Snake coordinates differ from the validated Layout")
    serialized_wires = {tuple(wire) for wire in decoded.get("wires", [])}
    expected_wires = {wire.as_factorio_tuple() for wire in result.layout.wires}
    if serialized_wires != expected_wires:
        raise ValueError("encoded Snake connector wires differ from the validated Layout")


def _placement_from_args(args: argparse.Namespace) -> PlacementOptions:
    if args.row_layout:
        return PlacementOptions(strategy="row", restarts=1)

    common = dict(
        strategy="annealed",
        corridor_width=args.corridor_width,
        target_fill=args.target_fill,
        random_seed=args.layout_seed,
        restarts=args.layout_retries,
        retry_fill_scale=0.8,
    )
    if args.net_aware_layout:
        return PlacementOptions(**common, iterations=args.annealing_iterations)
    if args.greedy_layout:
        return PlacementOptions(
            **common,
            iterations=0,
            block_width_tiles=8,
            block_height_tiles=8,
        )
    if args.linear_safe_layout:
        return safe_crossbar_options()

    return safe_folded_crossbar_options()


def _seed_lattice(layout: Layout) -> LegalPlacementLattice:
    """Build a generic half-tile lattice covering an existing benchmark seed envelope."""

    implementation = {entity.id: entity for entity in layout.circuit.entities}
    relay_ids = {relay.entity_id for relay in layout.relays}

    def sites_for(object_ids: list[int]) -> tuple[tuple[float, float], ...]:
        if not object_ids:
            return ()
        residues = {
            (layout.positions[object_id][0] % 1.0, layout.positions[object_id][1] % 1.0)
            for object_id in object_ids
        }
        min_x = floor(min(x for x, _y in layout.positions.values()))
        max_x = ceil(max(x for x, _y in layout.positions.values()))
        min_y = floor(min(y for _x, y in layout.positions.values()))
        max_y = ceil(max(y for _x, y in layout.positions.values()))
        return tuple(
            (float(x) + residue_x, float(y) + residue_y)
            for residue_x, residue_y in sorted(residues)
            for y in range(min_y, max_y + 1)
            for x in range(min_x, max_x + 1)
        )

    unit_ids = [
        object_id
        for object_id in layout.positions
        if object_id in relay_ids or isinstance(implementation[object_id], ConstantCombinator)
    ]
    wide_ids = [
        object_id
        for object_id in layout.positions
        if object_id not in relay_ids
        and not isinstance(implementation[object_id], ConstantCombinator)
    ]
    return LegalPlacementLattice(
        unit_sites=sites_for(unit_ids),
        wide_sites=sites_for(wide_ids),
    )


def _safe_folded_seed_problem(layout: Layout) -> LayoutOptimizationProblem:
    """Build the generic optimizer input used by the safe-folded benchmark mode."""

    marker_ids = {port.marker_entity for port in layout.circuit.inputs}
    marker_ids.update(port.marker_entity for port in layout.circuit.outputs)
    return LayoutOptimizationProblem(
        layout,
        _seed_lattice(layout),
        safe_wire_span=DEFAULT_SAFE_WIRE_SPAN,
        fixed_positions={entity_id: layout.positions[entity_id] for entity_id in marker_ids},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--steps-per-move",
        type=int,
        default=DEFAULT_LOGICAL_STEPS_PER_MOVE,
        help=(
            "advance Snake once per this many inferred periodic state occurrences "
            f"(default: {DEFAULT_LOGICAL_STEPS_PER_MOVE})"
        ),
    )
    parser.add_argument(
        "--optimize",
        action="store_true",
        help="enable vector packing; this also computes the compiler's unpacked comparison layout",
    )
    parser.add_argument(
        "--sampling-policy",
        choices=[policy.value for policy in SamplingPolicy],
        default=SamplingPolicy.ALAP.value,
        help=(
            "external Level observation policy; ALAP is the benchmark default, while "
            "beginning-of-step reproduces the phase-zero snapshot baseline"
        ),
    )
    parser.add_argument(
        "--census",
        action="store_true",
        help="print the pre-synthesis Abstract Physical IR census after compilation",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write the final blueprint string to this file instead of stdout",
    )
    placement_group = parser.add_mutually_exclusive_group()
    placement_group.add_argument(
        "--linear-safe-layout",
        action="store_true",
        help="use the already-proven one-row safe-crossbar reference/rollback layout",
    )
    placement_group.add_argument(
        "--greedy-layout",
        action="store_true",
        help="use deterministic greedy annealed-grid placement plus shared-net relay routing",
    )
    placement_group.add_argument(
        "--net-aware-layout",
        "--annealing-layout",
        dest="net_aware_layout",
        action="store_true",
        help=(
            "run bounded simulated annealing plus deterministic relaxation and shared-net relay "
            "routing"
        ),
    )
    placement_group.add_argument(
        "--row-layout",
        action="store_true",
        help="use the old one-dimensional diagnostic row placement (routing may be very slow)",
    )
    placement_group.add_argument(
        "--anneal-safe-folded-seed",
        action="store_true",
        help=(
            "construct the generic safe-folded layout first, then optimize that complete routed "
            "Layout through the fail-safe physical-layout annealer"
        ),
    )
    parser.add_argument(
        "--corridor-width",
        type=float,
        default=2.0,
        help="initial routing-corridor width for greedy/annealed layouts (default: 2.0)",
    )
    parser.add_argument(
        "--target-fill",
        type=float,
        default=0.60,
        help="initial candidate-slot fill for greedy/annealed layouts (default: 0.60)",
    )
    parser.add_argument(
        "--layout-retries",
        type=int,
        default=4,
        help="greedy/annealed placement/routing attempts before giving up (default: 4)",
    )
    parser.add_argument(
        "--annealing-iterations",
        type=int,
        default=1000,
        help=(
            "annealing proposals for --annealing-layout or --anneal-safe-folded-seed "
            "(default: 1000; the library auto-budget is intentionally not used by Snake)"
        ),
    )
    parser.add_argument(
        "--layout-seed",
        type=int,
        default=0,
        help="fixed annealer random seed (default: 0)",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="suppress compiler progress on stderr",
    )
    args = parser.parse_args()

    placement = _placement_from_args(args)
    terminal_progress = None if args.no_progress else _TerminalProgress()
    circuit = build_random_snake_circuit(logical_steps_per_move=args.steps_per_move)
    sampling_policy = SamplingPolicy(args.sampling_policy)
    try:
        result = circuit.compile(
            optimize=args.optimize,
            placement=placement,
            oracle_providers={
                FOOD_CANDIDATE_ORACLE: RandomSignalOracleProvider(update_interval=1),
            },
            sampling_policy=sampling_policy,
            progress=terminal_progress,
        )
    finally:
        if terminal_progress is not None:
            terminal_progress.close()

    if args.anneal_safe_folded_seed:
        optimization_started = monotonic()
        optimized = optimize_physical_layout(
            _safe_folded_seed_problem(result.layout),
            options=PlacementOptions(
                anchor_io=False,
                reserve_corridors=False,
                iterations=args.annealing_iterations,
                random_seed=args.layout_seed,
                restarts=1,
            ),
        )
        result = replace(
            result,
            layout=optimized.layout,
            blueprint_json=layout_to_blueprint_json(optimized.layout),
            blueprint_string=encode_layout_blueprint_string(optimized.layout),
        )
        print(
            "generic layout optimization: "
            f"input=({optimized.before.implementation_entities} implementation, "
            f"{optimized.before.relay_count} relays, "
            f"area {optimized.before.occupied_area:.1f}, "
            f"wire {optimized.before.wire_length:.1f}); "
            f"output=({optimized.after.implementation_entities} implementation, "
            f"{optimized.after.relay_count} relays, "
            f"area {optimized.after.occupied_area:.1f}, "
            f"wire {optimized.after.wire_length:.1f}); "
            f"work={optimized.proposal_budget} proposals; "
            f"runtime={monotonic() - optimization_started:.1f}s",
            file=sys.stderr,
        )
        for diagnostic in optimized.diagnostics:
            print(f"generic layout diagnostic: {diagnostic}", file=sys.stderr)

    if args.census:
        print(
            format_abstract_physical_census(census_abstract_physical(result.abstract_physical)),
            file=sys.stderr,
        )

    _validate_serialized_artifact(result)

    movement_port = next(port for port in result.physical_circuit.inputs if port.name == "movement")
    reset_port = next(port for port in result.physical_circuit.inputs if port.name == "reset")
    framebuffer_port = next(
        port for port in result.physical_circuit.outputs if port.name == "framebuffer"
    )
    movement_color = _marker_wire_color(result, movement_port.marker_entity)
    reset_color = _marker_wire_color(result, reset_port.marker_entity)
    framebuffer_color = _marker_wire_color(result, framebuffer_port.marker_entity)
    if reset_port.signal is None:
        raise ValueError("scalar reset port unexpectedly has no concrete signal")

    selectors = [
        entity
        for entity in result.blueprint_json["blueprint"]["entities"]  # type: ignore[index]
        if isinstance(entity, dict) and entity.get("name") == "selector-combinator"
    ]
    if not any(
        isinstance(entity.get("control_behavior"), dict)
        and entity["control_behavior"].get("operation") == "random"
        for entity in selectors
    ):
        raise ValueError("random-food Snake blueprint contains no Random Input selector")

    print(
        "snake: "
        f"combinators={result.physical_circuit.combinator_count}, "
        f"relays={len(result.layout.relays)}, "
        f"state_period={result.state_timing.uniform_period}, "
        f"random_food=selector, sampling={sampling_policy.value}",
        file=sys.stderr,
    )
    print(
        "wire movement detector -> INPUT movement with "
        f"{movement_color.value.upper()}; pulse INPUT reset "
        f"[{reset_port.signal.name}] nonzero with "
        f"{reset_color.value.upper()}; OUTPUT framebuffer -> display with "
        f"{framebuffer_color.value.upper()}",
        file=sys.stderr,
    )
    print(
        "front-panel marker positions (relative blueprint coordinates): "
        f"reset={result.layout.positions[reset_port.marker_entity]}, "
        f"movement={result.layout.positions[movement_port.marker_entity]}, "
        f"framebuffer={result.layout.positions[framebuffer_port.marker_entity]}",
        file=sys.stderr,
    )
    if args.output is None:
        print(result.blueprint_string)
    else:
        args.output.write_text(result.blueprint_string + "\n", encoding="utf-8")
        print(f"wrote blueprint to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
