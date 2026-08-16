"""Generate the first Snake circuit blueprint with a predictable physical-synthesis path.

The game model lives in :mod:`examples.snake`.  This wrapper defaults to row placement and disables
packing so first in-game iteration performs one deterministic synthesis instead of a layout benchmark.
Use the opt-in flags when intentionally stress-testing physical synthesis.
"""

from __future__ import annotations

import argparse

from examples.snake import (
    DEFAULT_LOGICAL_STEPS_PER_MOVE,
    _marker_wire_color,
    build_snake_circuit,
)
from factorio_circuit import compile_circuit
from factorio_circuit.synthesis.placement import PlacementOptions


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
        "--net-aware-layout",
        action="store_true",
        help="run the full net-aware placement optimizer instead of deterministic row placement",
    )
    args = parser.parse_args()

    placement = None if args.net_aware_layout else PlacementOptions(strategy="row")
    result = compile_circuit(
        build_snake_circuit(logical_steps_per_move=args.steps_per_move),
        optimize=args.optimize,
        placement=placement,
    )

    movement_port = next(port for port in result.physical_circuit.inputs if port.name == "movement")
    framebuffer_port = next(
        port for port in result.physical_circuit.outputs if port.name == "framebuffer"
    )
    movement_color = _marker_wire_color(result, movement_port.marker_entity)
    framebuffer_color = _marker_wire_color(result, framebuffer_port.marker_entity)

    print(
        "snake: "
        f"combinators={result.physical_circuit.combinator_count}, "
        f"state_period={result.state_timing.uniform_period}"
    )
    print(
        "wire movement detector -> INPUT movement with "
        f"{movement_color.value.upper()}; OUTPUT framebuffer -> display with "
        f"{framebuffer_color.value.upper()}"
    )
    print(result.blueprint_string)


if __name__ == "__main__":
    main()
