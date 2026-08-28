"""Contract multilevel Snake macros from the net-aware flat coarse seed, not safe-folded geometry."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from math import sqrt
from time import monotonic

from benchmarks.snake.multilevel_zoom_probe import (
    _build_seed,
    _entity_footprint_area,
    _implementation_geometry,
    _macro_order,
)
from factorio_circuit.ir.physical import ConstantCombinator
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis import layout_optimizer
from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.multilevel import MultilevelHierarchy, build_multilevel_hierarchy

Position = tuple[float, float]
_EPSILON = 1e-9


def _positions_from_flat_macro_zoom(
    state,
    grid,
    hierarchy: MultilevelHierarchy,
    reference_positions: dict[int, Position],
    *,
    zoom_scale: float,
) -> dict[int, Position] | None:
    fixed = {
        entity_id: position
        for entity_id, position in state.positions.items()
        if entity_id in state.fixed_objects
    }
    placement_state = incremental.exact._JointState(
        circuit=state.circuit,
        endpoints_by_group=state.endpoints_by_group,
        colors_by_group=state.colors_by_group,
        positions=dict(fixed),
        relay_positions={},
        relay_groups={},
        safe_span=state.safe_span,
        forbidden_areas=state.forbidden_areas,
        fixed_objects=state.fixed_objects,
    )
    occupancy = incremental._SpatialOccupancy.build(placement_state)
    entities = {entity.id: entity for entity in state.circuit.entities}
    movable = set(state.positions) - set(state.fixed_objects)
    source_center = incremental._centroid([reference_positions[item] for item in movable])
    target_center = layout_optimizer._coarse_target_center(state)

    source_left = min(reference_positions[item][0] - state.object_half_extent(item)[0] for item in movable)
    source_right = max(reference_positions[item][0] + state.object_half_extent(item)[0] for item in movable)
    source_top = min(reference_positions[item][1] - state.object_half_extent(item)[1] for item in movable)
    source_bottom = max(reference_positions[item][1] + state.object_half_extent(item)[1] for item in movable)
    source_width = max(_EPSILON, source_right - source_left)
    source_height = max(_EPSILON, source_bottom - source_top)
    movable_footprint = _entity_footprint_area(state, movable)
    target_area = max(movable_footprint / 0.90, source_width * source_height * zoom_scale**2)
    aspect = source_width / source_height
    target_width = sqrt(target_area * aspect)
    target_height = sqrt(target_area / aspect)
    margin = 2.0
    bounds = (
        target_center[0] - target_width / 2.0 - margin,
        target_center[0] + target_width / 2.0 + margin,
        target_center[1] - target_height / 2.0 - margin,
        target_center[1] + target_height / 2.0 + margin,
    )
    front_panel = layout_optimizer._fixed_public_front_panel(state)

    def allowed(position: Position) -> bool:
        left, right, top, bottom = bounds
        if not (left <= position[0] <= right and top <= position[1] <= bottom):
            return False
        if front_panel is None:
            return True
        panel_center, forward = front_panel
        projection = (position[0] - panel_center[0]) * forward[0] + (
            position[1] - panel_center[1]
        ) * forward[1]
        return projection >= 2.0 - _EPSILON

    unit_candidates = tuple(position for position in grid.unit_slots if allowed(position))
    wide_candidates = tuple(position for position in grid.slots if allowed(position))
    if not unit_candidates or not wide_candidates:
        return None

    level = hierarchy.levels[-1]
    peer_groups = layout_optimizer._entity_net_peer_groups(state)
    result = dict(fixed)
    macro_source_centers = {
        macro_index: incremental._centroid(
            [reference_positions[item] for item in macro.members if item in movable]
        )
        for macro_index, macro in enumerate(level.macros)
        if not macro.fixed
    }
    macro_targets = {
        macro_index: (
            target_center[0] + (center[0] - source_center[0]) * zoom_scale,
            target_center[1] + (center[1] - source_center[1]) * zoom_scale,
        )
        for macro_index, center in macro_source_centers.items()
    }

    degree: dict[int, int] = defaultdict(int)
    for edge in hierarchy.hyperedges:
        for entity_id in edge.members:
            degree[entity_id] += len(edge.members) - 1

    for macro_index in _macro_order(level, hierarchy.hyperedges):
        macro = level.macros[macro_index]
        if macro.fixed:
            continue
        macro_target = macro_targets[macro_index]
        members = sorted(macro.members, key=lambda entity_id: (-degree[entity_id], entity_id))
        placed_inside: list[Position] = []
        for entity_id in members:
            entity = entities[entity_id]
            candidates = unit_candidates if isinstance(entity, ConstantCombinator) else wide_candidates
            groups = [
                [result[peer] for peer in group if peer in result]
                for group in peer_groups.get(entity_id, ())
            ]
            groups = [group for group in groups if group]
            local_target = incremental._centroid(placed_inside) if placed_inside else macro_target

            def candidate_key(
                candidate: Position,
                *,
                groups: list[list[Position]] = groups,
                local_target: Position = local_target,
                macro_target: Position = macro_target,
            ):
                group_distances = [
                    min(incremental._distance(candidate, peer) for peer in group)
                    for group in groups
                ]
                violations = sum(distance > state.safe_span + _EPSILON for distance in group_distances)
                excess = sum(max(0.0, distance - state.safe_span) for distance in group_distances)
                return (
                    violations,
                    excess,
                    sum(group_distances),
                    (candidate[0] - local_target[0]) ** 2 + (candidate[1] - local_target[1]) ** 2,
                    (candidate[0] - macro_target[0]) ** 2 + (candidate[1] - macro_target[1]) ** 2,
                    candidate,
                )

            legal = (
                candidate
                for candidate in candidates
                if not occupancy.overlaps(entity_id, candidate, ignored=set())
            )
            try:
                position = min(legal, key=candidate_key)
            except ValueError:
                return None
            result[entity_id] = position
            placement_state.positions[entity_id] = position
            occupancy.add(entity_id, position)
            placed_inside.append(position)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scales", default="0.55,0.65,0.75,0.85,0.95")
    args = parser.parse_args()
    scales = tuple(float(item) for item in args.scales.split(","))
    if any(not 0.0 < scale <= 1.0 for scale in scales):
        parser.error("all scales must be in (0, 1]")

    started = monotonic()
    result, problem, state = _build_seed()
    grid = layout_optimizer._lattice_grid(problem.lattice)
    hierarchy = build_multilevel_hierarchy(
        result.physical_circuit,
        fixed_entities=frozenset(problem.fixed_positions),
        target_macros=32,
    )
    flat_positions = layout_optimizer._coarse_implementation_positions(state, grid)
    if flat_positions is None:
        raise RuntimeError("flat coarse legalizer failed")
    report = {
        "flat_coarse": _implementation_geometry(state, flat_positions, hierarchy.hyperedges),
        "hierarchy_macro_counts": [len(level.macros) for level in hierarchy.levels],
        "scales": {},
    }
    for scale in scales:
        scale_started = monotonic()
        positions = _positions_from_flat_macro_zoom(
            state,
            grid,
            hierarchy,
            flat_positions,
            zoom_scale=scale,
        )
        report["scales"][str(scale)] = {
            "runtime_seconds": monotonic() - scale_started,
            "geometry": None if positions is None else _implementation_geometry(
                state, positions, hierarchy.hyperedges
            ),
        }
    report["total_runtime_seconds"] = monotonic() - started
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
