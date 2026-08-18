"""Inspect Snake immediately after abstract physical lowering, before synthesis/layout."""

from __future__ import annotations

import argparse
import json
import sys
from time import monotonic

from benchmarks.snake.model import DEFAULT_LOGICAL_STEPS_PER_MOVE, build_snake_circuit
from factorio_circuit import CompileProgress, lower_to_abstract_physical
from factorio_circuit.analysis import (
    census_abstract_physical,
    census_phase_delays,
    format_abstract_physical_census,
    format_phase_delay_census,
)


class _Progress:
    def __init__(self) -> None:
        self._started = monotonic()

    def __call__(self, event: CompileProgress) -> None:
        detail = f": {event.detail}" if event.detail else ""
        print(
            f"[{monotonic() - self._started:7.1f}s] {event.phase}{detail}",
            file=sys.stderr,
            flush=True,
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
        help="enable the existing semantic/lowering packing before taking the census",
    )
    parser.add_argument(
        "--no-framebuffer",
        action="store_true",
        help="omit framebuffer state/rendering to isolate the core game-state realization",
    )
    parser.add_argument(
        "--deep-delays",
        action="store_true",
        help=(
            "reconstruct residual phase-delay trunks and report their sources, sinks, "
            "branching, and depths"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of the human-readable report",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="suppress lowering progress on stderr",
    )
    args = parser.parse_args()

    lowered = lower_to_abstract_physical(
        build_snake_circuit(
            logical_steps_per_move=args.steps_per_move,
            render_framebuffer=not args.no_framebuffer,
        ),
        optimize=args.optimize,
        progress=None if args.no_progress else _Progress(),
    )
    census = census_abstract_physical(lowered.abstract_physical)
    delay_census = (
        census_phase_delays(lowered.abstract_physical) if args.deep_delays else None
    )

    if args.json:
        payload: dict[str, object] = census.as_dict()
        if delay_census is not None:
            payload = {
                "abstract_physical": payload,
                "phase_delays": delay_census.as_dict(),
                "state_period": lowered.state_timing.uniform_period,
            }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_abstract_physical_census(census))
        print(f"  state period: {lowered.state_timing.uniform_period}")
        if delay_census is not None:
            print()
            print(format_phase_delay_census(delay_census))


if __name__ == "__main__":
    main()
