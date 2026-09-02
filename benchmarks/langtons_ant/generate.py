"""Compile and emit the interactive Langton's Ant benchmark blueprint."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from benchmarks.langtons_ant.model import build_langtons_ant_circuit
from factorio_circuit import CompilationResult
from factorio_circuit.ir.physical import WireColor
from factorio_circuit.synthesis.safe_crossbar import safe_crossbar_options
from factorio_circuit.synthesis.safe_folded_crossbar import safe_folded_crossbar_options


def _marker_wire_color(result: CompilationResult, marker_entity: int) -> WireColor:
    colors = {
        wire.color
        for wire in result.layout.wires
        if wire.source_entity == marker_entity or wire.target_entity == marker_entity
    }
    if len(colors) != 1:
        rendered = ", ".join(sorted(color.value for color in colors)) or "none"
        raise ValueError(
            f"expected one synthesized wire color at marker {marker_entity}; found {rendered}"
        )
    return next(iter(colors))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--optimize",
        action="store_true",
        help="enable semantic/vector optimization before physical lowering",
    )
    parser.add_argument(
        "--linear-safe-layout",
        action="store_true",
        help="use the one-row safe-crossbar reference layout instead of safe-folded",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write the blueprint string to this file instead of stdout",
    )
    args = parser.parse_args()

    placement = (
        safe_crossbar_options() if args.linear_safe_layout else safe_folded_crossbar_options()
    )
    result = build_langtons_ant_circuit().compile(optimize=args.optimize, placement=placement)

    movement_port = next(port for port in result.physical_circuit.inputs if port.name == "movement")
    framebuffer_port = next(
        port for port in result.physical_circuit.outputs if port.name == "framebuffer"
    )
    movement_color = _marker_wire_color(result, movement_port.marker_entity)
    framebuffer_color = _marker_wire_color(result, framebuffer_port.marker_entity)

    print(
        "langtons-ant: "
        f"combinators={result.physical_circuit.combinator_count}, "
        f"relays={len(result.layout.relays)}, "
        f"state_period={result.state_timing.uniform_period}",
        file=sys.stderr,
    )
    print(
        "wire movement detector -> INPUT movement with "
        f"{movement_color.value.upper()}; OUTPUT framebuffer -> lamp screen with "
        f"{framebuffer_color.value.upper()}",
        file=sys.stderr,
    )
    print(
        "controls: N run; S pause; E single-step; W reset; diagonal regions are neutral/re-arm",
        file=sys.stderr,
    )

    if args.output is None:
        print(result.blueprint_string)
    else:
        args.output.write_text(result.blueprint_string + "\n", encoding="utf-8")
        print(f"wrote blueprint to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
