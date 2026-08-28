"""Compare flat coarse packing with relay-blind macro zoom/legalization on Snake.

This opt-in Milestone C probe stops before physical rerouting.  Its purpose is to answer a cheaper
question first: whether the logical multilevel hierarchy can turn the failproof safe-folded
implementation geometry into a substantially denser, lower-net-span legal seed than the existing
flat coarse reseed.  Routed relays never participate in clustering or target generation.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from math import sqrt
from time import monotonic

from benchmarks.snake.generate import _safe_folded_seed_problem
from benchmarks.snake.random_model import (
    DEFAULT_LOGICAL_STEPS_PER_MOVE,
    FOOD_CANDIDATE_ORACLE,
    build_random_snake_circuit,
)
from factorio_circuit import RandomSignalOracleProvider, SamplingPolicy
from factorio_circuit.ir.physical import ConstantCombinator
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis import layout_optimizer
from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.multilevel import (
    CoarseningLevel,
    ImplementationHyperedge,
    MultilevelHierarchy,
    build_multilevel_hierarchy,
    macro_pair_affinities,
)
from factorio_circuit.synthesis.safe_folded_crossbar import safe_folded_crossbar_options

Position = tuple[float, float]
_EPSILON = 1e-9


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


def _macro_order(
    level: CoarseningLevel,
    hyperedges: tuple[ImplementationHyperedge, ...],
) -> list[int]:
    affinities = macro_pair_affinities(level, hyperedges)
    degree = [0.0 for _macro in level.macros]
    for (left, right), weight in affinities.items():
        degree[left] += weight
        degree[right] += weight

    placed = {index for index, macro in enumerate(level.macros) if macro.fixed}
    remaining = set(range(len(level.macros))) - placed
    order: list[int] = []
    while remaining:
        def key(index: int) -> tuple[float, float, tuple[int, ...]]:
            attached = sum(
                affinities.get(tuple(sorted((index, other))), 0.0) for other in placed
            )
            return (-attached, -degree[index], level.macros[index].members)

        chosen = min(remaining, key=key)
        remaining.remove(chosen)
        placed.add(chosen)
        order.append(chosen)
    return order


def _entity_footprint_area(state, entity_ids: set[int]) -> float:
    entities = {entity.id: entity for entity in state.circuit.entities}
    return sum(
        4.0
        * base_placement._entity_half_extent(entities[entity_id])[0]
        * base_placement._entity_half_extent(entities[entity_id])[1]
        for entity_id in entity_ids
    )


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
    area = (right - left) * (bottom - top)
    footprint = _entity_footprint_area(state, ids)
    hpwl = 0.0
    for edge in hyperedges:
        members = [entity_id for entity_id in edge.members if entity_id in positions]
        if len(members) <= 1:
            continue
        xs = [positions[entity_id][0] for entity_id in members]
        ys = [positions[entity_id][1] for entity_id in members]
        hpwl += max(xs) - min(xs) + max(ys) - min(ys)
    return {
        "footprint_area": footprint,
        "bounding_box_area": area,
        "occupancy": footprint / area if area > 0.0 else 1.0,
        "logical_hypernet_hpwl": hpwl,
        "width": right - left,
        "height": bottom - top,
    }


def _clustered_zoom_positions(
    state,
    grid,
    hierarchy: MultilevelHierarchy,
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
    source_center = incremental._centroid([state.positions[entity_id] for entity_id in movable])
    target_center = layout_optimizer._coarse_target_center(state)
    source_left, source_right, source_top, source_bottom = incremental._occupied_envelope(
        state,
        excluded=set(state.fixed_objects),
        include_relays=False,
    )
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
            [state.positions[entity_id] for entity_id in macro.members if entity_id in movable]
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
            groups = tuple(
                tuple(result[peer] for peer in group if peer in result)
                for group in peer_groups.get(entity_id, ())
            )
            groups = tuple(group for group in groups if group)
            local_target = incremental._centroid(placed_inside) if placed_inside else macro_target

            def candidate_key(
                candidate: Position,
                *,
                groups: tuple[tuple[Position, ...], ...] = groups,
                local_target: Position = local_target,
                macro_target: Position = macro_target,
            ) -> tuple[int, float, float, float, float, Position]:
                group_distances = [
                    min(incremental._distance(candidate, peer) for peer in group)
                    for group in groups
                ]
                violations = sum(
                    distance > state.safe_span + _EPSILON for distance in group_distances
                )
                excess = sum(
                    max(0.0, distance - state.safe_span) for distance in group_distances
                )
                return (
                    violations,
                    excess,
                    sum(group_distances),
                    (candidate[0] - local_target[0]) ** 2
                    + (candidate[1] - local_target[1]) ** 2,
                    (candidate[0] - macro_target[0]) ** 2
                    + (candidate[1] - macro_target[1]) ** 2,
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
    parser.add_argument("--target-macros", type=int, default=32)
    parser.add_argument("--zoom-scale", type=float, default=0.15)
    args = parser.parse_args()
    if args.target_macros <= 0:
        parser.error("--target-macros must be positive")
    if not 0.0 < args.zoom_scale <= 1.0:
        parser.error("--zoom-scale must be in (0, 1]")

    started = monotonic()
    result, problem, state = _build_seed()
    grid = layout_optimizer._lattice_grid(problem.lattice)
    hierarchy = build_multilevel_hierarchy(
        result.physical_circuit,
        fixed_entities=frozenset(problem.fixed_positions),
        target_macros=args.target_macros,
    )
    source_positions = dict(state.positions)
    flat_positions = layout_optimizer._coarse_implementation_positions(state, grid)
    macro_positions = _clustered_zoom_positions(
        state,
        grid,
        hierarchy,
        zoom_scale=args.zoom_scale,
    )
    if flat_positions is None or macro_positions is None:
        raise RuntimeError("one of the coarse implementation legalizers failed")

    report = {
        "benchmark": "snake-multilevel-zoom",
        "target_macros": args.target_macros,
        "zoom_scale": args.zoom_scale,
        "hierarchy_macro_counts": [len(level.macros) for level in hierarchy.levels],
        "source": _implementation_geometry(state, source_positions, hierarchy.hyperedges),
        "flat_coarse": _implementation_geometry(state, flat_positions, hierarchy.hyperedges),
        "macro_zoom": _implementation_geometry(state, macro_positions, hierarchy.hyperedges),
        "runtime_seconds": monotonic() - started,
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
