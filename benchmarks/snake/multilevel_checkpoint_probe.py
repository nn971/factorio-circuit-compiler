"""Run the first full Snake C6 uncoarsen -> C5 transactional reroute checkpoint."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from time import monotonic

from benchmarks.snake.multilevel_zoom_probe import _build_seed, _implementation_geometry
from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.layout_optimizer import physical_layout_metrics
from factorio_circuit.synthesis.multilevel import build_multilevel_hierarchy
from factorio_circuit.synthesis.multilevel_anneal import MacroAnnealOptions, anneal_macro_geometry
from factorio_circuit.synthesis.multilevel_uncoarsen import (
    HierarchicalUncoarsenOptions,
    hierarchical_uncoarsen,
    legalize_singleton_implementation_targets,
)
from factorio_circuit.synthesis.multilevel_zoom import build_macro_geometry, compact_macro_geometry
from factorio_circuit.synthesis.transactional_reroute import reroute_implementation_transactionally


def _physical_occupancy(layout) -> dict[str, float]:
    entities = {entity.id: entity for entity in layout.circuit.entities}
    implementation_area = sum(
        4.0
        * base_placement._entity_half_extent(entity)[0]
        * base_placement._entity_half_extent(entity)[1]
        for entity in entities.values()
    )
    relay_area = float(len(layout.relays))
    numerator = implementation_area + relay_area
    denominator = physical_layout_metrics(layout).occupied_area
    return {
        "implementation_area": implementation_area,
        "relay_area": relay_area,
        "numerator": numerator,
        "denominator": denominator,
        "ratio": numerator / denominator if denominator else 1.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-macros", type=int, default=32)
    parser.add_argument("--macro-density", type=float, default=0.80)
    parser.add_argument("--coarse-proposals", type=int, default=8192)
    parser.add_argument("--per-level-proposals", type=int, default=256)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--final-site-search-radius", type=int, default=64)
    parser.add_argument("--routing-margin-scale", type=float, default=2.0)
    args = parser.parse_args()

    report: dict[str, object] = {
        "benchmark": "snake-c6-c5-full-checkpoint",
        "parameters": vars(args),
        "runtime_seconds": {},
    }
    runtime = report["runtime_seconds"]
    assert isinstance(runtime, dict)

    started = monotonic()
    result, problem, state = _build_seed()
    runtime["snake_compile_and_seed_validation"] = monotonic() - started

    started = monotonic()
    hierarchy = build_multilevel_hierarchy(
        result.physical_circuit,
        fixed_entities=frozenset(problem.fixed_positions),
        target_macros=args.target_macros,
    )
    runtime["hierarchy"] = monotonic() - started
    report["hierarchy_macro_counts"] = [len(level.macros) for level in hierarchy.levels]

    source = build_macro_geometry(
        result.physical_circuit,
        state.positions,
        hierarchy.levels[-1],
        target_density=args.macro_density,
    )
    started = monotonic()
    zoom = compact_macro_geometry(source, hierarchy.hyperedges)
    runtime["c3_macro_zoom"] = monotonic() - started
    if zoom.accepted_scale is None:
        report["failed_stage"] = "c3_macro_zoom"
        report["failure"] = "no contracting legal macro zoom candidate"
        print(json.dumps(report, indent=2, sort_keys=True))
        return

    started = monotonic()
    coarse = anneal_macro_geometry(
        zoom.geometry,
        hierarchy.hyperedges,
        options=MacroAnnealOptions(
            proposals=args.coarse_proposals,
            random_seed=args.seed,
            max_area_factor=1.04,
        ),
    )
    runtime["c4_coarse_anneal"] = monotonic() - started
    report["c4"] = {
        "zoom": asdict(zoom.after),
        "annealed": asdict(coarse.after),
        "stats": asdict(coarse.stats),
    }

    started = monotonic()
    try:
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
    except ValueError as exc:
        runtime["c6_hierarchical_uncoarsen"] = monotonic() - started
        report["failed_stage"] = "c6_hierarchical_uncoarsen"
        report["failure"] = str(exc)
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    runtime["c6_hierarchical_uncoarsen"] = monotonic() - started
    report["c6_levels"] = [
        {
            "level_index": item.level_index,
            "macro_count": item.macro_count,
            "target_density": item.target_density,
            "expanded": asdict(item.expanded),
            "refined": asdict(item.refined),
            "accepted_zoom_scale": item.accepted_zoom_scale,
            "anneal_stats": asdict(item.anneal_stats),
        }
        for item in uncoarsened.levels
    ]

    started = monotonic()
    try:
        implementation_positions = legalize_singleton_implementation_targets(
            problem,
            uncoarsened.geometry,
            search_radius=args.final_site_search_radius,
        )
    except ValueError as exc:
        runtime["c6_final_lattice_projection"] = monotonic() - started
        report["failed_stage"] = "c6_final_lattice_projection"
        report["failure"] = str(exc)
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    runtime["c6_final_lattice_projection"] = monotonic() - started
    report["projected_implementation"] = _implementation_geometry(
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
    runtime["c5_transactional_reroute"] = monotonic() - started
    report["reroute"] = {
        "succeeded": rerouted.succeeded,
        "failure": rerouted.failure,
        "routing_unit_sites": rerouted.routing_unit_sites,
        "before": asdict(rerouted.before),
        "after": asdict(rerouted.after),
    }
    if not rerouted.succeeded:
        report["failed_stage"] = "c5_transactional_reroute"
        report["failure"] = rerouted.failure
        print(json.dumps(report, indent=2, sort_keys=True))
        return

    occupancy = _physical_occupancy(rerouted.layout)
    report["physical_occupancy"] = occupancy
    relay_count = len(rerouted.layout.relays)
    implementation_count = rerouted.layout.circuit.combinator_count
    report["relay_efficiency"] = {
        "implementation_combinators": implementation_count,
        "relay_combinators": relay_count,
        "implementation_per_relay": (
            implementation_count / relay_count if relay_count else None
        ),
    }
    report["passes_80_percent_occupancy"] = occupancy["ratio"] > 0.80
    report["total_runtime_seconds"] = sum(float(value) for value in runtime.values())
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
