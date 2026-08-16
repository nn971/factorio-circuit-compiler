"""Generate the first Snake circuit blueprint with observable, predictable synthesis.

The game model lives in :mod:`examples.snake`. The default uses the deterministic greedy seed of the
net-aware placer (zero optimization iterations, one attempt): it keeps connected logic local without
turning the first in-game iteration into a placement-optimization benchmark. Progress is printed to
stderr while the final importable blueprint string remains on stdout.
"""

from __future__ import annotations

import argparse
import sys
from time import monotonic

from examples.snake import (
    DEFAULT_LOGICAL_STEPS_PER_MOVE,
    _marker_wire_color,
    build_snake_circuit,
)
from factorio_circuit import CompileProgress, compile_circuit
from factorio_circuit.synthesis.placement import PlacementOptions


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
                f"\r[{self._elapsed()}] {event.phase:14} [{bar}] "
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


def _placement_from_args(args: argparse.Namespace) -> PlacementOptions:
    if args.row_layout:
        return PlacementOptions(strategy="row", restarts=1)
    if args.net_aware_layout:
        return PlacementOptions(strategy="net-aware")
    # Fast default: use the topology-aware greedy seed, but skip annealing/relaxation and retries.
    return PlacementOptions(strategy="net-aware", iterations=0, restarts=1)


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
    placement_group = parser.add_mutually_exclusive_group()
    placement_group.add_argument(
        "--net-aware-layout",
        action="store_true",
        help="run the full iterative net-aware placement optimizer",
    )
    placement_group.add_argument(
        "--row-layout",
        action="store_true",
        help="use the old one-dimensional diagnostic row placement (routing may be very slow)",
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

    movement_port = next(port for port in result.physical_circuit.inputs if port.name == "movement")
    framebuffer_port = next(
        port for port in result.physical_circuit.outputs if port.name == "framebuffer"
    )
    movement_color = _marker_wire_color(result, movement_port.marker_entity)
    framebuffer_color = _marker_wire_color(result, framebuffer_port.marker_entity)

    print(
        "snake: "
        f"combinators={result.physical_circuit.combinator_count}, "
        f"state_period={result.state_timing.uniform_period}",
        file=sys.stderr,
    )
    print(
        "wire movement detector -> INPUT movement with "
        f"{movement_color.value.upper()}; OUTPUT framebuffer -> display with "
        f"{framebuffer_color.value.upper()}",
        file=sys.stderr,
    )
    print(result.blueprint_string)


if __name__ == "__main__":
    main()
