"""Generate an experimental Snake blueprint with within-period Level delays bypassed.

This runner is intentionally separate from the canonical compiler.  It preserves the periodic clock
startup delay and any alignment request spanning a full state period, while ordinary shorter scalar
and vector phase-alignment requests are represented by direct reuse of the same net.

The resulting blueprint is a gameplay probe, not a proven-correct compiler output.  Its purpose is to
measure how much of the current delay hardware is unnecessary when Level values persist naturally.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from time import monotonic

from benchmarks.snake.generate import _TerminalProgress, _marker_wire_color
from benchmarks.snake.model import DEFAULT_LOGICAL_STEPS_PER_MOVE, build_snake_circuit
from factorio_circuit.analysis import (
    analyze_normalized_state_timing,
    census_abstract_physical,
    format_abstract_physical_census,
)
from factorio_circuit.blueprint.layout_encode import encode_layout_blueprint_string
from factorio_circuit.experimental.delay_bypass_lowering import lower_with_delay_bypass
from factorio_circuit.ir.output import preserve_output_materializations
from factorio_circuit.lowering.frontend_to_ir import lower_frontend
from factorio_circuit.synthesis.open_vector import synthesize_vector_layout
from factorio_circuit.synthesis.safe_folded_crossbar import safe_folded_crossbar_options


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--steps-per-move",
        type=int,
        default=DEFAULT_LOGICAL_STEPS_PER_MOVE,
    )
    parser.add_argument(
        "--no-framebuffer",
        action="store_true",
        help="omit framebuffer/body-pixel state",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("snake-delay-bypass-blueprint.txt"),
        help="blueprint output path (default: snake-delay-bypass-blueprint.txt)",
    )
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args()

    started = monotonic()
    source = build_snake_circuit(
        logical_steps_per_move=args.steps_per_move,
        render_framebuffer=not args.no_framebuffer,
    )
    built = source.build()
    semantic = preserve_output_materializations(lower_frontend(built), built.output)
    normalized_at = monotonic()
    timing = analyze_normalized_state_timing(semantic)
    timed_at = monotonic()
    period = timing.uniform_period
    if period is None:
        raise ValueError("delay-bypass Snake probe requires one uniform state period")

    abstract, stats = lower_with_delay_bypass(
        semantic,
        state_timing=timing,
        enable_packing=False,
    )
    lowered_at = monotonic()

    print("experimental Snake delay-bypass lowering", file=sys.stderr)
    print(
        "  bypassed alignment requests: "
        f"scalar={stats.scalar_alignment_calls_bypassed} calls/"
        f"{stats.scalar_alignment_ticks_bypassed} ticks; "
        f"vector={stats.vector_alignment_calls_bypassed} calls/"
        f"{stats.vector_alignment_ticks_bypassed} ticks",
        file=sys.stderr,
    )
    print(
        "  preserved alignment requests: "
        f"scalar={stats.scalar_alignment_calls_preserved} calls/"
        f"{stats.scalar_alignment_ticks_preserved} ticks; "
        f"vector={stats.vector_alignment_calls_preserved} calls/"
        f"{stats.vector_alignment_ticks_preserved} ticks; "
        f"startup={stats.startup_delay_ticks_preserved} ticks",
        file=sys.stderr,
    )
    print(format_abstract_physical_census(census_abstract_physical(abstract)), file=sys.stderr)

    terminal_progress = None if args.no_progress else _TerminalProgress()
    try:
        layout = synthesize_vector_layout(
            abstract,
            placement=safe_folded_crossbar_options(),
            progress=terminal_progress,
        )
    finally:
        if terminal_progress is not None:
            terminal_progress.close()
    synthesized_at = monotonic()

    blueprint = encode_layout_blueprint_string(layout)
    encoded_at = monotonic()
    args.output.write_text(blueprint + "\n", encoding="utf-8")

    movement_port = next(port for port in layout.circuit.inputs if port.name == "movement")
    reset_port = next(port for port in layout.circuit.inputs if port.name == "reset")
    framebuffer_port = next(
        (port for port in layout.circuit.outputs if port.name == "framebuffer"),
        None,
    )
    movement_color = _marker_wire_color(
        type("ResultView", (), {"layout": layout})(), movement_port.marker_entity
    )
    reset_color = _marker_wire_color(
        type("ResultView", (), {"layout": layout})(), reset_port.marker_entity
    )

    print(
        "experimental snake: "
        f"combinators={layout.circuit.combinator_count}; "
        f"relays={len(layout.relays)}; state_period={period}",
        file=sys.stderr,
    )
    if reset_port.signal is not None:
        print(
            "wire movement detector -> INPUT movement with "
            f"{movement_color.value.upper()}; pulse INPUT reset "
            f"[{reset_port.signal.name}] nonzero with {reset_color.value.upper()}",
            file=sys.stderr,
        )
    if framebuffer_port is not None:
        framebuffer_color = _marker_wire_color(
            type("ResultView", (), {"layout": layout})(), framebuffer_port.marker_entity
        )
        print(
            "OUTPUT framebuffer -> display with "
            f"{framebuffer_color.value.upper()}",
            file=sys.stderr,
        )
    print(
        "  timings: "
        f"normalization={normalized_at - started:.1f}s; "
        f"timing={timed_at - normalized_at:.1f}s; "
        f"experimental_lowering={lowered_at - timed_at:.1f}s; "
        f"synthesis_layout={synthesized_at - lowered_at:.1f}s; "
        f"encode={encoded_at - synthesized_at:.1f}s",
        file=sys.stderr,
    )
    print(f"wrote experimental blueprint to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
