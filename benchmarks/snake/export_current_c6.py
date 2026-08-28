"""Export the current accepted C3 -> C4 -> C6 -> C5 Snake physical artifact."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path

from benchmarks.snake.multilevel_checkpoint_probe import _physical_occupancy
from benchmarks.snake.multilevel_zoom_probe import _build_seed
from factorio_circuit.blueprint.layout_encode import (
    encode_layout_blueprint_string,
    layout_to_blueprint_json,
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
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    result, problem, state = _build_seed()
    hierarchy = build_multilevel_hierarchy(
        result.physical_circuit,
        fixed_entities=frozenset(problem.fixed_positions),
        target_macros=32,
    )
    source = build_macro_geometry(
        result.physical_circuit,
        state.positions,
        hierarchy.levels[-1],
        target_density=0.80,
    )
    zoom = compact_macro_geometry(source, hierarchy.hyperedges)
    if zoom.accepted_scale is None:
        raise RuntimeError("C3 macro zoom found no legal contracting candidate")
    coarse = anneal_macro_geometry(
        zoom.geometry,
        hierarchy.hyperedges,
        options=MacroAnnealOptions(proposals=8192, random_seed=0, max_area_factor=1.04),
    )
    uncoarsened = hierarchical_uncoarsen(
        result.physical_circuit,
        state.positions,
        hierarchy,
        coarse.geometry,
        options=HierarchicalUncoarsenOptions(
            target_density=0.80,
            finest_density=1.0,
            proposals_per_level=256,
            random_seed=0,
            max_area_factor=1.04,
            local_search_radius=2,
            final_site_search_radius=64,
        ),
    )
    implementation_positions = legalize_singleton_implementation_targets(
        problem,
        uncoarsened.geometry,
        search_radius=64,
    )
    rerouted = reroute_implementation_transactionally(
        problem,
        implementation_positions,
        footprint_margin_scale=2.0,
    )
    if not rerouted.succeeded:
        raise RuntimeError(f"C5 reroute failed: {rerouted.failure}")
    validate_physical_layout(replace(problem, layout=rerouted.layout))

    blueprint_json = layout_to_blueprint_json(rerouted.layout)
    blueprint_string = encode_layout_blueprint_string(rerouted.layout)
    occupancy = _physical_occupancy(rerouted.layout)
    metadata = {
        "stage": "merged C6 / fresh C5 checkpoint",
        "parameters": {
            "target_macros": 32,
            "macro_density": 0.80,
            "c4_proposals": 8192,
            "per_level_proposals": 256,
            "seed": 0,
            "final_site_search_radius": 64,
            "routing_margin_scale": 2.0,
        },
        "hierarchy_macro_counts": [len(level.macros) for level in hierarchy.levels],
        "reroute": {
            "before": asdict(rerouted.before),
            "after": asdict(rerouted.after),
            "routing_unit_sites": rerouted.routing_unit_sites,
        },
        "physical_occupancy": occupancy,
    }

    (args.output_dir / "snake-current-c6-c5.txt").write_text(blueprint_string + "\n")
    (args.output_dir / "snake-current-c6-c5.blueprint.json").write_text(
        json.dumps(blueprint_json, indent=2, sort_keys=True) + "\n"
    )
    (args.output_dir / "snake-current-c6-c5.metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
