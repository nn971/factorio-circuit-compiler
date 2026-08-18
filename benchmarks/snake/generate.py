"""Compile and emit the heavyweight interactive Snake benchmark blueprint.

The default uses ``safe-folded-crossbar``: deterministic serpentine entity rows, row-local physical
bus tracks chosen after fold portals are known, and search-free vertical stitches. Public inputs and
outputs are clustered at the beginning of the first row. The simpler linear ``safe-crossbar`` remains
available explicitly as a canonical rollback path.

Progress is printed to stderr. The final importable blueprint string is printed to stdout unless
``--output`` names a file. Greedy, full net-aware/annealing, row, and linear-safe layouts remain
explicit diagnostic/reference modes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from time import monotonic

from benchmarks.snake.model import DEFAULT_LOGICAL_STEPS_PER_MOVE, build_snake_circuit
from factorio_circuit import CompilationResult, CompileProgress, compile_circuit
from factorio_circuit.analysis import census_abstract_physical, format_abstract_physical_census
from factorio_circuit.ir.physical import WireColor
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
            f"expected exactly one synthesized wire color at marker {marker_entity}; found {rendered}"
        )
    return next(iter(colors))


def _placement_from_args(args: argparse.Namespace) -> PlacementOptions:
    if args.row_layout:
        return PlacementOptions(strategy="row", restarts=1)

    common = dict(
        strategy="net-aware",
        corridor_width=args.corridor_width,
        target_fill=args.target_fill,
        restarts=args.layout_retries,
        retry_fill_scale=0.8,
    )
    if args.net_aware_layout:
        return PlacementOptions(**common)
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
        help="use deterministic greedy net-aware placement plus collision-aware routing",
    )
    placement_group.add_argument(
        "--net-aware-layout",
        "--annealing-layout",
        dest="net_aware_layout",
        action="store_true",
        help=(
            "run the previous simulated-annealing net-aware placer, followed by deterministic "
            "relaxation and the heuristic router"
        ),
    )
    placement_group.add_argument(
        "--row-layout",
        action="store_true",
        help="use the old one-dimensional diagnostic row placement (routing may be very slow)",
    )
    parser.add_argument(
        "--corridor-width",
        type=float,
        default=4.0,
        help="initial routing-corridor width for greedy/net-aware layouts (default: 4.0)",
    )
    parser.add_argument(
        "--target-fill",
        type=float,
        default=0.60,
        help="initial candidate-slot fill for greedy/net-aware layouts (default: 0.60)",
    )
    parser.add_argument(
        "--layout-retries",
        type=int,
        default=4,
        help="greedy/net-aware placement/routing attempts before giving up (default: 4)",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="suppress compiler progress on stderr",
    )
    args = parser.parse_args()

    placement = _placement_from_args(args)
    terminal_progress = None if args.no_progress else _TerminalProgress()
    try:
        result = compile_circuit(
            build_snake_circuit(logical_steps_per_move=args.steps_per_move),
            optimize=args.optimize,
            placement=placement,
            progress=terminal_progress,
        )
    finally:
        if terminal_progress is not None:
            terminal_progress.close()

    if args.census:
        print(
            format_abstract_physical_census(census_abstract_physical(result.abstract_physical)),
            file=sys.stderr,
        )

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

    print(
        "snake: "
        f"combinators={result.physical_circuit.combinator_count}, "
        f"relays={len(result.layout.relays)}, "
        f"state_period={result.state_timing.uniform_period}",
        file=sys.stderr,
    )
    print(
        "wire movement detector -> INPUT movement with "
        f"{movement_color.value.upper()}; pulse INPUT reset [{reset_port.signal.name}] nonzero with "
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
