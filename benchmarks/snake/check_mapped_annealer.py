"""Temporary application check: apply the generic physical annealer to mapped Snake."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from time import monotonic

from benchmarks.snake import generate_mapping
from benchmarks.snake.generate import _safe_folded_seed_problem
from factorio_circuit.synthesis.layout_optimizer import optimize_physical_layout
from factorio_circuit.synthesis.placement import PlacementOptions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposals", type=int, default=512)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--period", type=int, default=60)
    parser.add_argument("--time-limit", type=float, default=300.0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    original_synthesize = generate_mapping.synthesize_vector_layout

    def synthesize_then_anneal(*synth_args, **synth_kwargs):
        layout = original_synthesize(*synth_args, **synth_kwargs)
        started = monotonic()
        optimized = optimize_physical_layout(
            _safe_folded_seed_problem(layout),
            options=PlacementOptions(
                anchor_io=False,
                reserve_corridors=False,
                iterations=args.proposals,
                random_seed=args.seed,
                restarts=1,
            ),
        )
        print(
            "mapped generic layout optimization: "
            f"input=({optimized.before.implementation_entities} implementation, "
            f"{optimized.before.relay_count} relays, "
            f"area {optimized.before.occupied_area:.1f}, "
            f"wire {optimized.before.wire_length:.1f}); "
            f"output=({optimized.after.implementation_entities} implementation, "
            f"{optimized.after.relay_count} relays, "
            f"area {optimized.after.occupied_area:.1f}, "
            f"wire {optimized.after.wire_length:.1f}); "
            f"work={optimized.proposal_budget} proposals; "
            f"runtime={monotonic() - started:.1f}s",
            file=sys.stderr,
        )
        for diagnostic in optimized.diagnostics:
            print(f"mapped generic layout diagnostic: {diagnostic}", file=sys.stderr)
        return optimized.layout

    generate_mapping.synthesize_vector_layout = synthesize_then_anneal
    sys.argv = [
        "generate_mapping",
        "--period",
        str(args.period),
        "--time-limit",
        str(args.time_limit),
        "--workers",
        str(args.workers),
        "--no-progress",
        "--output",
        str(args.output),
    ]
    generate_mapping.main()


if __name__ == "__main__":
    main()
