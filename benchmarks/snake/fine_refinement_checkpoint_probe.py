"""Run the full Snake C3-C7 physical-layout checkpoint and report exact occupancy."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from time import monotonic

from benchmarks.snake.multilevel_checkpoint_probe import _physical_occupancy
from benchmarks.snake.multilevel_zoom_probe import _build_seed, _implementation_geometry
from factorio_circuit.synthesis.fine_routed_refinement import (
    FineRefinementOptions,
    refine_routed_layout_transactionally,
)
from factorio_circuit.synthesis.layout_optimizer import validate_physical_layout
from factorio_circuit.synthesis.multilevel import build_multilevel_hierarchy
from factorio_circuit.synthesis.multilevel_anneal import MacroAnnealOptions, anneal_macro_geometry
from factorio_circuit.synthesis.multilevel_uncoarsen import (
    HierarchicalUncoarsenOptions,
    hierarchical_uncoarsen,
    legalize_singleton_implementation_targets,
)
from factorio_circuit.synthesis.multilevel_zoom import build_macro_geometry, compact_macro_geometry
from factorio_circuit.synthesis.transactional_reroute import reroute_implementation_transactionally


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-macros", type=int, default=32)
    parser.add_argument("--macro-density", type=float, default=0.80)
    parser.add_argument("--coarse-proposals", type=int, default=8192)
    parser.add_argument("--per-level-proposals", type=int, default=256)
    parser.add_argument("--fine-proposals", type=int, default=4096)
    parser.add_argument("--fine-chunk-size", type=int, default=255)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--final-site-search-radius", type=int, default=64)
    parser.add_argument("--routing-margin-scale", type=float, default=2.0)
    args = parser.parse_args()

    report: dict[str, object] = {
        "benchmark": "snake-c7-full-checkpoint",
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
    report["c6_final"] = asdict(uncoarsened.levels[-1].refined)

    started = monotonic()
    implementation_positions = legalize_singleton_implementation_targets(
        problem,
        uncoarsened.geometry,
        search_radius=args.final_site_search_radius,
    )
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
    report["c5"] = {
        "succeeded": rerouted.succeeded,
        "failure": rerouted.failure,
        "routing_unit_sites": rerouted.routing_unit_sites,
        "before": asdict(rerouted.before),
        "after": asdict(rerouted.after),
        "physical_occupancy": _physical_occupancy(rerouted.layout),
    }
    if not rerouted.succeeded:
        report["failed_stage"] = "c5_transactional_reroute"
        report["failure"] = rerouted.failure
        print(json.dumps(report, indent=2, sort_keys=True))
        return

    routed_problem = replace(problem, layout=rerouted.layout)
    validate_physical_layout(routed_problem)
    started = monotonic()
    fine = refine_routed_layout_transactionally(
        routed_problem,
        options=FineRefinementOptions(
            proposals=args.fine_proposals,
            random_seed=args.seed,
            chunk_size=args.fine_chunk_size,
        ),
    )
    runtime["c7_fine_refinement"] = monotonic() - started
    final_problem = replace(problem, layout=fine.layout)
    validate_physical_layout(final_problem)
    final_occupancy = _physical_occupancy(fine.layout)
    report["c7"] = {
        "accepted": fine.accepted,
        "before": asdict(fine.before),
        "after": asdict(fine.after),
        "diagnostics": list(fine.diagnostics),
        "physical_occupancy": final_occupancy,
    }
    report["passes_80_percent_occupancy"] = final_occupancy["ratio"] > 0.80
    report["total_runtime_seconds"] = sum(float(value) for value in runtime.values())
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
