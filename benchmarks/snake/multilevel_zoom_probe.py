"""Measure genuine relay-blind coarse macro zoom on full Snake.

This opt-in Milestone C C2+C3 probe stops before uncoarsening and physical rerouting. The coarsest
hierarchy level remains a small set of abstract rectangular macros throughout legalization; the probe
does not expand those macros back to hundreds of implementation entities. Routed relays never
participate in clustering, macro footprints, target generation, or coarse legalization.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from time import monotonic

from benchmarks.snake.generate import _safe_folded_seed_problem
from benchmarks.snake.random_model import (
    DEFAULT_LOGICAL_STEPS_PER_MOVE,
    FOOD_CANDIDATE_ORACLE,
    build_random_snake_circuit,
)
from factorio_circuit import RandomSignalOracleProvider, SamplingPolicy
from factorio_circuit.synthesis import layout_optimizer
from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.multilevel import build_multilevel_hierarchy
from factorio_circuit.synthesis.multilevel_zoom import (
    build_macro_geometry,
    compact_macro_geometry,
    macro_placement_metrics,
    validate_macro_placement,
)
from factorio_circuit.synthesis.safe_folded_crossbar import safe_folded_crossbar_options

Position = tuple[float, float]


def _build_seed():
    result = build_random_snake_circuit(
        logical_steps_per_move=DEFAULT_LOGICAL_STEPS_PER_MOVE
    ).compile(
        optimize=False,
        placement=safe_folded_crossbar_options(),
        oracle_providers={
            FOOD_CANDIDATE_ORACLE: RandomSignalOracleProvider(update_interval=1),
        },
        sampling_policy=SamplingPolicy.ALAP,
        progress=None,
    )
    problem = _safe_folded_seed_problem(result.layout)
    embedding = layout_optimizer._validated_embedding(problem)
    return result, problem, embedding.state


def _implementation_geometry(state, positions: dict[int, Position], hyperedges):
    entities = {entity.id: entity for entity in state.circuit.entities}
    ids = set(positions)
    left = min(
        positions[entity_id][0] - base_placement._entity_half_extent(entities[entity_id])[0]
        for entity_id in ids
    )
    right = max(
        positions[entity_id][0] + base_placement._entity_half_extent(entities[entity_id])[0]
        for entity_id in ids
    )
    top = min(
        positions[entity_id][1] - base_placement._entity_half_extent(entities[entity_id])[1]
        for entity_id in ids
    )
    bottom = max(
        positions[entity_id][1] + base_placement._entity_half_extent(entities[entity_id])[1]
        for entity_id in ids
    )
    footprint = sum(
        4.0
        * base_placement._entity_half_extent(entities[entity_id])[0]
        * base_placement._entity_half_extent(entities[entity_id])[1]
        for entity_id in ids
    )
    area = (right - left) * (bottom - top)
    hpwl = 0.0
    for edge in hyperedges:
        xs = [positions[entity_id][0] for entity_id in edge.members]
        ys = [positions[entity_id][1] for entity_id in edge.members]
        hpwl += max(xs) - min(xs) + max(ys) - min(ys)
    return {
        "footprint_area": footprint,
        "bounding_box_area": area,
        "occupancy": footprint / area if area else 1.0,
        "logical_hypernet_hpwl": hpwl,
        "width": right - left,
        "height": bottom - top,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-macros", type=int, default=32)
    parser.add_argument("--macro-density", type=float, default=0.80)
    args = parser.parse_args()
    if args.target_macros <= 0:
        parser.error("--target-macros must be positive")
    if not 0.0 < args.macro_density <= 1.0:
        parser.error("--macro-density must be in (0, 1]")

    compile_started = monotonic()
    result, problem, state = _build_seed()
    compile_runtime = monotonic() - compile_started

    hierarchy_started = monotonic()
    hierarchy = build_multilevel_hierarchy(
        result.physical_circuit,
        fixed_entities=frozenset(problem.fixed_positions),
        target_macros=args.target_macros,
    )
    hierarchy_runtime = monotonic() - hierarchy_started
    level = hierarchy.levels[-1]

    flat_started = monotonic()
    grid = layout_optimizer._lattice_grid(problem.lattice)
    flat_positions = layout_optimizer._coarse_implementation_positions(state, grid)
    flat_runtime = monotonic() - flat_started
    if flat_positions is None:
        raise RuntimeError("existing flat coarse implementation legalizer failed")

    source_macro = build_macro_geometry(
        result.physical_circuit,
        state.positions,
        level,
        target_density=args.macro_density,
    )
    source_macro_metrics = macro_placement_metrics(source_macro, hierarchy.hyperedges)
    flat_macro = build_macro_geometry(
        result.physical_circuit,
        flat_positions,
        level,
        target_density=args.macro_density,
    )
    flat_projected_macro_metrics = macro_placement_metrics(flat_macro, hierarchy.hyperedges)

    zoom_started = monotonic()
    zoom = compact_macro_geometry(source_macro, hierarchy.hyperedges)
    zoom_runtime = monotonic() - zoom_started
    if zoom.accepted_scale is None:
        raise RuntimeError("coarse macro zoom found no contracting legal candidate")
    validate_macro_placement(zoom.geometry)

    report = {
        "benchmark": "snake-genuine-coarse-macro-zoom",
        "hierarchy_macro_counts": [len(item.macros) for item in hierarchy.levels],
        "coarsest_macros": len(level.macros),
        "fixed_macros": sum(macro.fixed for macro in level.macros),
        "largest_macro_members": max(len(macro.members) for macro in level.macros),
        "macro_density": args.macro_density,
        "runtime_seconds": {
            "snake_compile_and_seed_validation": compile_runtime,
            "hierarchy": hierarchy_runtime,
            "existing_flat_entity_legalizer": flat_runtime,
            "genuine_macro_zoom": zoom_runtime,
        },
        "source_implementation": _implementation_geometry(
            state,
            dict(state.positions),
            hierarchy.hyperedges,
        ),
        "flat_implementation": _implementation_geometry(
            state,
            flat_positions,
            hierarchy.hyperedges,
        ),
        "source_macro_metrics": asdict(source_macro_metrics),
        "flat_projected_macro_metrics": asdict(flat_projected_macro_metrics),
        "macro_zoom": {
            "accepted_scale": zoom.accepted_scale,
            "rejected_scales": zoom.rejected_scales,
            "after": asdict(zoom.after),
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
