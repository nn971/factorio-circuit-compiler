"""Generate an in-game Snake blueprint through the temporal technology mapper.

This is an opt-in benchmark path. It does not change ``compile_circuit()``: the Snake semantic
module is extracted into the implementation-neutral periodic mapping problem, solved jointly for
operation/state timing and scalar delay buses, lowered from the selected ``RealizationPlan`` to
Abstract Physical IR, and then handed to the established physical synthesis/layout/blueprint
backend.

The mapped benchmark currently uses ``benchmarks.snake.model`` and therefore the deterministic food
sequence encoded by that model. Random Input selector/oracle technology mapping is a later
milestone.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from time import monotonic

from factorio_circuit.blueprint.layout_encode import (
    encode_layout_blueprint_string,
    layout_to_blueprint_json,
)
from factorio_circuit.blueprint.routing import DEFAULT_SAFE_WIRE_SPAN
from factorio_circuit.ir.physical import WireColor
from factorio_circuit.lowering.frontend_to_ir import lower_frontend
from factorio_circuit.mapping import (
    build_periodic_state_mapping_problem,
    lower_periodic_state_mapping_plan,
    ordinary_candidates,
    ordinary_state_candidates,
    solve_periodic_state_bus_mapping_problem,
)
from factorio_circuit.progress import CompileProgress
from factorio_circuit.sampling import SamplingPolicy
from factorio_circuit.synthesis.open_vector import synthesize_vector_layout
from factorio_circuit.synthesis.placement import PlacementOptions
from factorio_circuit.synthesis.safe_crossbar import safe_crossbar_options
from factorio_circuit.synthesis.safe_folded_crossbar import safe_folded_crossbar_options

from .model import build_snake_circuit


class _TerminalProgress:
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
            print(
                f"\r[{self._elapsed()}] {event.phase:18} [{bar}] "
                f"{event.completed}/{event.total}{detail}",
                end="",
                file=sys.stderr,
                flush=True,
            )
            self._inline = True
            return
        self._finish_inline()
        detail = f": {event.detail}" if event.detail else ""
        print(f"[{self._elapsed()}] {event.phase}{detail}", file=sys.stderr, flush=True)

    def close(self) -> None:
        self._finish_inline()


def _marker_wire_color(layout, marker_entity: int) -> WireColor:
    colors = {
        wire.color
        for wire in layout.wires
        if wire.source_entity == marker_entity or wire.target_entity == marker_entity
    }
    if len(colors) != 1:
        rendered = ", ".join(sorted(color.value for color in colors)) or "none"
        raise ValueError(
            "expected exactly one synthesized wire color at marker "
            f"{marker_entity}; found {rendered}"
        )
    return next(iter(colors))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--period",
        type=int,
        default=60,
        help="logical period in ticks (default: 60)",
    )
    parser.add_argument(
        "--max-delay-buses",
        type=int,
        default=1,
        help="maximum shared scalar delay buses (default: 1)",
    )
    parser.add_argument(
        "--bus-capacity",
        type=int,
        default=256,
        help="maximum scalar lanes on one bus (default: 256)",
    )
    parser.add_argument(
        "--time-limit",
        type=float,
        default=300.0,
        help="CP-SAT time limit in seconds (default: 300)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="CP-SAT worker count (default: 8)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("snake-mapped-blueprint.txt"),
        help="blueprint output path (default: snake-mapped-blueprint.txt)",
    )
    parser.add_argument(
        "--linear-safe-layout",
        action="store_true",
        help="use the one-row safe-crossbar reference layout instead of safe-folded-crossbar",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="suppress synthesis/layout progress",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.period < 3:
        raise SystemExit("--period must be at least 3 for the mapped periodic commit resource")

    module = lower_frontend(build_snake_circuit(render_framebuffer=True))
    output_phase = 2 * args.period - 1
    problem = build_periodic_state_mapping_problem(
        module,
        period=args.period,
        output_phases=(output_phase,) * len(module.output.values),
        sampling_policy=SamplingPolicy.ALAP,
    )
    operation_candidates = ordinary_candidates(problem)
    state_candidates = ordinary_state_candidates(problem)

    print("solving mapped Snake recurrence...", file=sys.stderr)
    solve = solve_periodic_state_bus_mapping_problem(
        problem,
        candidates=operation_candidates,
        state_candidates=state_candidates,
        max_delay_buses=args.max_delay_buses,
        delay_bus_capacity=args.bus_capacity,
        time_limit_seconds=args.time_limit,
        workers=args.workers,
    )
    if not solve.proven_optimal:
        raise RuntimeError(
            "mapped Snake blueprint generation requires a proven-optimal plan; "
            f"solver returned {solve.status}"
        )

    lowered = lower_periodic_state_mapping_plan(
        module,
        problem,
        operation_candidates,
        state_candidates,
        solve.plan,
    )
    if not lowered.cost_exact_after_known_surcharges:
        raise RuntimeError(
            "mapped physical lowering has an unexplained cost gap: "
            f"{lowered.unexplained_cost_gap:+d}"
        )

    placement: PlacementOptions = (
        safe_crossbar_options() if args.linear_safe_layout else safe_folded_crossbar_options()
    )
    progress = None if args.no_progress else _TerminalProgress()
    try:
        layout = synthesize_vector_layout(
            lowered.circuit,
            safe_wire_span=DEFAULT_SAFE_WIRE_SPAN,
            placement=placement,
            progress=progress,
        )
    finally:
        if progress is not None:
            progress.close()

    blueprint_json = layout_to_blueprint_json(layout)
    blueprint_string = encode_layout_blueprint_string(layout)
    args.output.write_text(blueprint_string + "\n", encoding="utf-8")

    movement_port = next(port for port in layout.circuit.inputs if port.name == "movement")
    reset_port = next(port for port in layout.circuit.inputs if port.name == "reset")
    framebuffer_port = next(port for port in layout.circuit.outputs if port.name == "framebuffer")
    movement_color = _marker_wire_color(layout, movement_port.marker_entity)
    reset_color = _marker_wire_color(layout, reset_port.marker_entity)
    framebuffer_color = _marker_wire_color(layout, framebuffer_port.marker_entity)
    if reset_port.signal is None:
        raise ValueError("scalar reset port unexpectedly has no concrete signal")

    if not isinstance(blueprint_json.get("blueprint"), dict):
        raise ValueError("encoded mapped Snake result is not a Factorio blueprint")

    print(
        "mapped snake: "
        f"plan={solve.plan.total_cost}, abstract={lowered.emitted_combinators}, "
        f"physical={layout.circuit.combinator_count}, relays={len(layout.relays)}, "
        f"period={args.period}, buses={len(solve.plan.delay_buses)}, food=deterministic",
        file=sys.stderr,
    )
    print(
        "wire movement detector -> INPUT movement with "
        f"{movement_color.value.upper()}; pulse INPUT reset "
        f"[{reset_port.signal.name}] nonzero with {reset_color.value.upper()}; "
        "OUTPUT framebuffer -> display with "
        f"{framebuffer_color.value.upper()}",
        file=sys.stderr,
    )
    print(
        "front-panel marker positions (relative blueprint coordinates): "
        f"reset={layout.positions[reset_port.marker_entity]}, "
        f"movement={layout.positions[movement_port.marker_entity]}, "
        f"framebuffer={layout.positions[framebuffer_port.marker_entity]}",
        file=sys.stderr,
    )
    print(f"wrote mapped Snake blueprint to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
