"""Measure C4 coarse macro annealing after the genuine C3 zoom on full Snake."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from time import monotonic

from benchmarks.snake.multilevel_zoom_probe import _build_seed
from factorio_circuit.synthesis.multilevel import build_multilevel_hierarchy
from factorio_circuit.synthesis.multilevel_anneal import (
    MacroAnnealOptions,
    anneal_macro_geometry,
    coarse_cut_congestion,
)
from factorio_circuit.synthesis.multilevel_zoom import (
    build_macro_geometry,
    compact_macro_geometry,
    validate_macro_placement,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposals", type=int, default=8192)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--target-macros", type=int, default=32)
    parser.add_argument("--macro-density", type=float, default=0.80)
    parser.add_argument("--max-area-factor", type=float, default=1.04)
    args = parser.parse_args()
    if args.proposals < 0:
        parser.error("--proposals must be non-negative")
    if args.target_macros <= 0:
        parser.error("--target-macros must be positive")
    if not 0.0 < args.macro_density <= 1.0:
        parser.error("--macro-density must be in (0, 1]")
    if args.max_area_factor < 1.0:
        parser.error("--max-area-factor must be at least 1")

    seed_started = monotonic()
    result, problem, state = _build_seed()
    hierarchy = build_multilevel_hierarchy(
        result.physical_circuit,
        fixed_entities=frozenset(problem.fixed_positions),
        target_macros=args.target_macros,
    )
    level = hierarchy.levels[-1]
    source = build_macro_geometry(
        result.physical_circuit,
        state.positions,
        level,
        target_density=args.macro_density,
    )
    zoom = compact_macro_geometry(source, hierarchy.hyperedges)
    if zoom.accepted_scale is None:
        raise RuntimeError("C3 macro zoom found no contracting legal candidate")
    validate_macro_placement(zoom.geometry)
    seed_runtime = monotonic() - seed_started

    anneal_started = monotonic()
    annealed = anneal_macro_geometry(
        zoom.geometry,
        hierarchy.hyperedges,
        options=MacroAnnealOptions(
            proposals=args.proposals,
            random_seed=args.seed,
            max_area_factor=args.max_area_factor,
        ),
    )
    anneal_runtime = monotonic() - anneal_started
    validate_macro_placement(annealed.geometry)

    print(
        json.dumps(
            {
                "benchmark": "snake-coarse-macro-anneal",
                "hierarchy_macro_counts": [len(item.macros) for item in hierarchy.levels],
                "coarsest_macros": len(level.macros),
                "fixed_macros": sum(macro.fixed for macro in level.macros),
                "proposals": args.proposals,
                "seed": args.seed,
                "max_area_factor": args.max_area_factor,
                "runtime_seconds": {
                    "compile_hierarchy_and_zoom": seed_runtime,
                    "macro_anneal": anneal_runtime,
                },
                "zoom": {
                    "accepted_scale": zoom.accepted_scale,
                    "metrics": asdict(zoom.after),
                    "congestion": coarse_cut_congestion(zoom.geometry, hierarchy.hyperedges),
                },
                "annealed": {
                    "metrics": asdict(annealed.after),
                    "congestion": annealed.after_congestion,
                    "energy": asdict(annealed.after_energy),
                    "stats": asdict(annealed.stats),
                },
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
