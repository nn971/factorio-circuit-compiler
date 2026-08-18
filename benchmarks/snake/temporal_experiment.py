"""Inspect Snake as an experimental phase-free temporal computation hypergraph.

This runner does not invoke timing, physical lowering, synthesis, layout, or blueprint encoding.  It
exists only to exercise the isolated research IR under ``factorio_circuit.experimental``.
"""

from __future__ import annotations

import argparse
from collections import Counter
from time import monotonic

from benchmarks.snake.model import build_snake_circuit
from factorio_circuit.experimental.temporal_hypergraph import build_level_temporal_hypergraph
from factorio_circuit.lowering.frontend_to_ir import lower_frontend


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-framebuffer",
        action="store_true",
        help="omit framebuffer and body-pixel state from the Snake workload",
    )
    args = parser.parse_args()

    started = monotonic()
    module = lower_frontend(
        build_snake_circuit(render_framebuffer=not args.no_framebuffer)
    )
    normalized_at = monotonic()
    graph = build_level_temporal_hypergraph(module)
    built_at = monotonic()

    print(graph.summary())
    print(
        "  timings: "
        f"normalization={normalized_at - started:.1f}s; "
        f"hypergraph={built_at - normalized_at:.1f}s"
    )
    observation_kinds = Counter(item.label.split(":", 1)[0] for item in graph.observations)
    print("  observations by boundary:")
    for label, count in sorted(
        observation_kinds.items(), key=lambda item: (-item[1], item[0])
    ):
        print(f"    {label}: {count}")


if __name__ == "__main__":
    main()
