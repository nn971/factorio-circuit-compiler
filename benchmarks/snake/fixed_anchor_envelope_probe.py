"""Measure the exact footprint envelope imposed by Snake's fixed public markers."""

from __future__ import annotations

import json

from benchmarks.snake.multilevel_zoom_probe import _build_seed, _implementation_geometry
from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.multilevel import build_multilevel_hierarchy


def main() -> None:
    result, problem, state = _build_seed()
    entities = {entity.id: entity for entity in result.physical_circuit.entities}
    fixed = sorted(problem.fixed_positions)
    if not fixed:
        raise RuntimeError("Snake optimization problem unexpectedly has no fixed markers")

    left = min(
        problem.fixed_positions[entity_id][0]
        - base_placement._entity_half_extent(entities[entity_id])[0]
        for entity_id in fixed
    )
    right = max(
        problem.fixed_positions[entity_id][0]
        + base_placement._entity_half_extent(entities[entity_id])[0]
        for entity_id in fixed
    )
    top = min(
        problem.fixed_positions[entity_id][1]
        - base_placement._entity_half_extent(entities[entity_id])[1]
        for entity_id in fixed
    )
    bottom = max(
        problem.fixed_positions[entity_id][1]
        + base_placement._entity_half_extent(entities[entity_id])[1]
        for entity_id in fixed
    )
    width = right - left
    height = bottom - top

    hierarchy = build_multilevel_hierarchy(
        result.physical_circuit,
        fixed_entities=frozenset(fixed),
        target_macros=32,
    )
    report = {
        "benchmark": "snake-fixed-anchor-envelope",
        "fixed_count": len(fixed),
        "fixed_ids": fixed,
        "fixed_positions": {
            str(entity_id): problem.fixed_positions[entity_id] for entity_id in fixed
        },
        "fixed_envelope": {
            "left": left,
            "right": right,
            "top": top,
            "bottom": bottom,
            "width": width,
            "height": height,
            "area": width * height,
        },
        "source_implementation": _implementation_geometry(
            state,
            dict(state.positions),
            hierarchy.hyperedges,
        ),
        "coarsest_fixed_macros": sum(macro.fixed for macro in hierarchy.levels[-1].macros),
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
