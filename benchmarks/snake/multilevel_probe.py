"""Inspect relay-blind multilevel coarsening and coarse global zoom on full Snake.

This is an opt-in structural probe for Milestone C. It constructs the ordinary failproof safe-folded
Snake so that the exact physical implementation circuit is available, then builds a hierarchy using
only implementation electrical nets and fixed public markers. Routed relays and current relay
distances do not participate in clustering or coarse geometry.

The final hierarchy level is represented by compact abstract macro rectangles derived from
implementation footprint area. A global zoom contracts and legalizes those rectangles without
attempting to preserve the safe-folded relay scaffold. This is deliberately a C2+C3 proof rather than
a physical-layout result; transactional implementation expansion and relay rebuilding belong to C5.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from statistics import median
from time import monotonic

from benchmarks.snake.random_model import (
    DEFAULT_LOGICAL_STEPS_PER_MOVE,
    FOOD_CANDIDATE_ORACLE,
    build_random_snake_circuit,
)
from factorio_circuit import RandomSignalOracleProvider, SamplingPolicy
from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.multilevel import build_multilevel_hierarchy
from factorio_circuit.synthesis.multilevel_zoom import (
    build_macro_geometry,
    compact_macro_geometry,
)
from factorio_circuit.synthesis.safe_folded_crossbar import safe_folded_crossbar_options


def _implementation_geometry(result: object) -> dict[str, float]:
    layout = result.layout  # type: ignore[attr-defined]
    circuit = result.physical_circuit  # type: ignore[attr-defined]
    entities = {entity.id: entity for entity in circuit.entities}
    left = float("inf")
    right = float("-inf")
    top = float("inf")
    bottom = float("-inf")
    footprint = 0.0
    for entity_id, entity in entities.items():
        x, y = layout.positions[entity_id]
        half_x, half_y = base_placement._entity_half_extent(entity)
        footprint += 4.0 * half_x * half_y
        left = min(left, x - half_x)
        right = max(right, x + half_x)
        top = min(top, y - half_y)
        bottom = max(bottom, y + half_y)
    if not entities:
        return {
            "width": 0.0,
            "height": 0.0,
            "bounding_area": 0.0,
            "footprint_area": 0.0,
            "occupancy": 1.0,
        }
    width = right - left
    height = bottom - top
    area = width * height
    return {
        "width": width,
        "height": height,
        "bounding_area": area,
        "footprint_area": footprint,
        "occupancy": 1.0 if area == 0.0 else footprint / area,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-macros", type=int, default=32)
    parser.add_argument("--macro-density", type=float, default=0.85)
    parser.add_argument("--zoom-scales", default="0.25,0.35,0.50,0.65,0.80,0.90")
    args = parser.parse_args()
    if args.target_macros <= 0:
        parser.error("--target-macros must be positive")
    if not 0.0 < args.macro_density <= 1.0:
        parser.error("--macro-density must be in (0, 1]")
    scales = tuple(float(value) for value in args.zoom_scales.split(",") if value)
    if not scales:
        parser.error("--zoom-scales must contain at least one scale")

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
    fixed = frozenset(
        {
            *(port.marker_entity for port in result.physical_circuit.inputs),
            *(port.marker_entity for port in result.physical_circuit.outputs),
        }
    )
    hierarchy = build_multilevel_hierarchy(
        result.physical_circuit,
        fixed_entities=fixed,
        target_macros=args.target_macros,
    )

    levels = []
    for index, level in enumerate(hierarchy.levels):
        sizes = [len(macro.members) for macro in level.macros]
        levels.append(
            {
                "level": index,
                "macro_count": len(level.macros),
                "minimum_macro_members": min(sizes, default=0),
                "median_macro_members": median(sizes) if sizes else 0,
                "maximum_macro_members": max(sizes, default=0),
                "fixed_macros": sum(macro.fixed for macro in level.macros),
            }
        )

    coarse = build_macro_geometry(
        result.physical_circuit,
        result.layout.positions,
        hierarchy.levels[-1],
        target_density=args.macro_density,
    )
    started = monotonic()
    zoom = compact_macro_geometry(coarse, hierarchy.hyperedges, scales=scales)
    zoom_runtime = monotonic() - started
    source_geometry = _implementation_geometry(result)

    print(
        json.dumps(
            {
                "benchmark": "snake-multilevel-coarsening-and-zoom",
                "implementation_combinators": result.physical_circuit.combinator_count,
                "physical_entities": result.physical_circuit.blueprint_entity_count,
                "logical_hyperedges": len(hierarchy.hyperedges),
                "maximum_hyperedge_members": max(
                    (len(edge.members) for edge in hierarchy.hyperedges),
                    default=0,
                ),
                "target_macros": args.target_macros,
                "macro_density": args.macro_density,
                "levels": levels,
                "source_implementation_geometry": source_geometry,
                "coarse_zoom": {
                    "accepted_scale": zoom.accepted_scale,
                    "rejected_scales": zoom.rejected_scales,
                    "runtime_seconds": zoom_runtime,
                    "before": asdict(zoom.before),
                    "after": asdict(zoom.after),
                    "source_to_after_area_ratio": (
                        0.0
                        if source_geometry["bounding_area"] == 0.0
                        else zoom.after.bounding_area / source_geometry["bounding_area"]
                    ),
                },
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
