"""Sweep final singleton global zooms and exact-reroute each legal Snake candidate."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from time import monotonic

from benchmarks.snake.multilevel_checkpoint_probe import _physical_occupancy
from benchmarks.snake.multilevel_zoom_probe import _build_seed, _implementation_geometry
from factorio_circuit.synthesis.layout_optimizer import validate_physical_layout
from factorio_circuit.synthesis.multilevel import build_multilevel_hierarchy
from factorio_circuit.synthesis.multilevel_anneal import MacroAnnealOptions, anneal_macro_geometry
from factorio_circuit.synthesis.multilevel_uncoarsen import (
    HierarchicalUncoarsenOptions,
    hierarchical_uncoarsen,
    legalize_singleton_implementation_targets,
)
from factorio_circuit.synthesis.multilevel_zoom import (
    build_macro_geometry,
    compact_macro_geometry,
    macro_placement_metrics,
    try_macro_zoom,
)
from factorio_circuit.synthesis.transactional_reroute import reroute_implementation_transactionally


def _parse_scales(raw: str) -> tuple[float, ...]:
    scales = tuple(float(value.strip()) for value in raw.split(",") if value.strip())
    if not scales or any(not 0.0 < scale < 1.0 for scale in scales):
        raise argparse.ArgumentTypeError("zoom scales must be comma-separated values in (0, 1)")
    return scales


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-macros", type=int, default=32)
    parser.add_argument("--macro-density", type=float, default=0.80)
    parser.add_argument("--coarse-proposals", type=int, default=8192)
    parser.add_argument("--per-level-proposals", type=int, default=256)
    parser.add_argument("--scales", type=_parse_scales, default=(0.80, 0.85, 0.90))
    parser.add_argument("--zoom-radius", type=int, default=12)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--final-site-search-radius", type=int, default=64)
    parser.add_argument("--routing-margin-scale", type=float, default=2.0)
    args = parser.parse_args()

    report: dict[str, object] = {
        "benchmark": "snake-singleton-zoom-checkpoint",
        "parameters": {
            **vars(args),
            "scales": list(args.scales),
        },
        "runtime_seconds": {},
        "candidates": [],
    }
    runtime = report["runtime_seconds"]
    candidates = report["candidates"]
    assert isinstance(runtime, dict)
    assert isinstance(candidates, list)

    started = monotonic()
    result, problem, state = _build_seed()
    runtime["snake_compile_and_seed_validation"] = monotonic() - started

    hierarchy = build_multilevel_hierarchy(
        result.physical_circuit,
        fixed_entities=frozenset(problem.fixed_positions),
        target_macros=args.target_macros,
    )
    report["hierarchy_macro_counts"] = [len(level.macros) for level in hierarchy.levels]

    source = build_macro_geometry(
        result.physical_circuit,
        state.positions,
        hierarchy.levels[-1],
        target_density=args.macro_density,
    )
    zoom = compact_macro_geometry(source, hierarchy.hyperedges)
    if zoom.accepted_scale is None:
        raise RuntimeError("C3 coarse macro zoom found no contracting candidate")

    coarse = anneal_macro_geometry(
        zoom.geometry,
        hierarchy.hyperedges,
        options=MacroAnnealOptions(
            proposals=args.coarse_proposals,
            random_seed=args.seed,
            max_area_factor=1.04,
        ),
    )

    started = monotonic()
    uncoarsened = hierarchical_uncoarsen(
        result.physical_circuit,
        state.positions,
        hierarchy,
        coarse.geometry,
        options=HierarchicalUncoarsenOptions(
            target_density=args.macro_density,
            finest_density=1.0,
            proposals_per_level=args.per_level_proposals,
            random_seed=args.seed,
            max_area_factor=1.04,
            local_search_radius=2,
            final_site_search_radius=args.final_site_search_radius,
        ),
    )
    runtime["c6_hierarchical_uncoarsen"] = monotonic() - started
    report["c6_final"] = asdict(macro_placement_metrics(uncoarsened.geometry, hierarchy.hyperedges))

    baseline_positions = legalize_singleton_implementation_targets(
        problem,
        uncoarsened.geometry,
        search_radius=args.final_site_search_radius,
    )
    report["baseline_projected_implementation"] = _implementation_geometry(
        state,
        baseline_positions,
        hierarchy.hyperedges,
    )

    for scale in args.scales:
        row: dict[str, object] = {"scale": scale}
        started = monotonic()
        candidate, failure = try_macro_zoom(
            uncoarsened.geometry,
            scale=scale,
            max_legalization_radius=args.zoom_radius,
        )
        row["zoom_runtime_seconds"] = monotonic() - started
        if candidate is None:
            row["succeeded"] = False
            row["failed_stage"] = "singleton_zoom"
            row["failure"] = failure
            candidates.append(row)
            continue
        row["macro_metrics"] = asdict(macro_placement_metrics(candidate, hierarchy.hyperedges))

        started = monotonic()
        try:
            implementation_positions = legalize_singleton_implementation_targets(
                problem,
                candidate,
                search_radius=args.final_site_search_radius,
            )
        except ValueError as exc:
            row["projection_runtime_seconds"] = monotonic() - started
            row["succeeded"] = False
            row["failed_stage"] = "lattice_projection"
            row["failure"] = str(exc)
            candidates.append(row)
            continue
        row["projection_runtime_seconds"] = monotonic() - started
        row["projected_implementation"] = _implementation_geometry(
            state,
            implementation_positions,
            hierarchy.hyperedges,
        )

        started = monotonic()
        rerouted = reroute_implementation_transactionally(
            problem,
            implementation_positions,
            footprint_margin_scale=args.routing_margin_scale,
        )
        row["reroute_runtime_seconds"] = monotonic() - started
        row["reroute"] = {
            "succeeded": rerouted.succeeded,
            "failure": rerouted.failure,
            "routing_unit_sites": rerouted.routing_unit_sites,
            "after": asdict(rerouted.after),
        }
        if not rerouted.succeeded:
            row["succeeded"] = False
            row["failed_stage"] = "transactional_reroute"
            row["failure"] = rerouted.failure
            candidates.append(row)
            continue

        validate_physical_layout(replace(problem, layout=rerouted.layout))
        occupancy = _physical_occupancy(rerouted.layout)
        row["succeeded"] = True
        row["physical_occupancy"] = occupancy
        row["passes_80_percent_occupancy"] = occupancy["ratio"] > 0.80
        candidates.append(row)

    successful = [row for row in candidates if row.get("succeeded")]
    if successful:
        best = max(
            successful,
            key=lambda row: row["physical_occupancy"]["ratio"],
        )
        report["best_scale"] = best["scale"]
        report["best_physical_occupancy"] = best["physical_occupancy"]
        report["passes_80_percent_occupancy"] = best["passes_80_percent_occupancy"]
    else:
        report["best_scale"] = None
        report["passes_80_percent_occupancy"] = False

    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
