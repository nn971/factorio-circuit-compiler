"""Measure transactional global zoom compaction on the failproof Snake layout."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from time import monotonic

from benchmarks.snake.layout_acceptance import (
    _build_failproof_seed,
    application_layout_metrics,
)
from factorio_circuit.synthesis.global_zoom import compact_by_global_zoom
from factorio_circuit.synthesis.layout_optimizer import validate_physical_layout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scales", default="0.80,0.90,0.95,0.975")
    parser.add_argument("--max-passes", type=int, default=4)
    args = parser.parse_args()
    scales = tuple(float(value) for value in args.scales.split(",") if value)

    layout, problem = _build_failproof_seed()
    before = application_layout_metrics(layout, problem=problem, check_redundancy=False)
    started = monotonic()
    result = compact_by_global_zoom(problem, scales=scales, max_passes=args.max_passes)
    runtime = monotonic() - started
    final_problem = replace(problem, layout=result.layout)
    validate_physical_layout(final_problem)
    after = application_layout_metrics(result.layout, problem=final_problem)

    print(
        json.dumps(
            {
                "benchmark": "snake-global-zoom",
                "scales": scales,
                "max_passes": args.max_passes,
                "runtime_seconds": runtime,
                "accepted_scales": result.accepted_scales,
                "rejected_candidate_count": len(result.rejected_scales),
                "before": asdict(before),
                "after": asdict(after),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
