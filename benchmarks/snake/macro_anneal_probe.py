"""Anneal relay-blind Snake macro centers, then unpack them with the generic member legalizer."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from math import exp, sqrt
from random import Random
from time import monotonic

from benchmarks.snake.multilevel_flat_zoom_probe import _positions_from_flat_macro_zoom
from benchmarks.snake.multilevel_zoom_probe import (
    _build_seed,
    _implementation_geometry,
)
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis import layout_optimizer
from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.multilevel import (
    CoarseningLevel,
    ImplementationHyperedge,
    build_multilevel_hierarchy,
)

Position = tuple[float, float]
_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class MacroShape:
    half_width: float
    half_height: float


def _macro_owner(level: CoarseningLevel) -> dict[int, int]:
    return {
        entity_id: macro_index
        for macro_index, macro in enumerate(level.macros)
        for entity_id in macro.members
    }


def _macro_centers(
    level: CoarseningLevel,
    positions: dict[int, Position],
) -> dict[int, Position]:
    return {
        macro_index: incremental._centroid([positions[item] for item in macro.members])
        for macro_index, macro in enumerate(level.macros)
    }


def _macro_shapes(state, level: CoarseningLevel, *, local_fill: float = 0.72) -> dict[int, MacroShape]:
    entities = {entity.id: entity for entity in state.circuit.entities}
    result: dict[int, MacroShape] = {}
    for macro_index, macro in enumerate(level.macros):
        if macro.fixed and len(macro.members) == 1:
            half_x, half_y = state.object_half_extent(macro.members[0])
            result[macro_index] = MacroShape(half_x, half_y)
            continue
        footprint = 0.0
        for entity_id in macro.members:
            half_x, half_y = base_placement._entity_half_extent(entities[entity_id])
            footprint += 4.0 * half_x * half_y
        box_area = footprint / local_fill
        width = max(2.0, sqrt(box_area))
        height = max(1.0, box_area / width)
        result[macro_index] = MacroShape(width / 2.0, height / 2.0)
    return result


def _macro_hpwl(
    level: CoarseningLevel,
    hyperedges: tuple[ImplementationHyperedge, ...],
    centers: dict[int, Position],
) -> float:
    owner = _macro_owner(level)
    total = 0.0
    for edge in hyperedges:
        touched = sorted({owner[item] for item in edge.members})
        if len(touched) <= 1:
            continue
        xs = [centers[index][0] for index in touched]
        ys = [centers[index][1] for index in touched]
        total += max(xs) - min(xs) + max(ys) - min(ys)
    return total


def _overlap_area(
    centers: dict[int, Position],
    shapes: dict[int, MacroShape],
    movable: set[int],
) -> float:
    total = 0.0
    indexes = sorted(centers)
    for offset, left in enumerate(indexes):
        for right in indexes[offset + 1 :]:
            if left not in movable and right not in movable:
                continue
            left_shape = shapes[left]
            right_shape = shapes[right]
            dx = abs(centers[left][0] - centers[right][0])
            dy = abs(centers[left][1] - centers[right][1])
            overlap_x = max(0.0, left_shape.half_width + right_shape.half_width - dx)
            overlap_y = max(0.0, left_shape.half_height + right_shape.half_height - dy)
            total += overlap_x * overlap_y
    return total


def _envelope_overflow(
    centers: dict[int, Position],
    shapes: dict[int, MacroShape],
    movable: set[int],
    envelope: tuple[float, float, float, float],
) -> float:
    left, right, top, bottom = envelope
    total = 0.0
    for index in movable:
        x, y = centers[index]
        shape = shapes[index]
        total += max(0.0, left - (x - shape.half_width)) ** 2
        total += max(0.0, (x + shape.half_width) - right) ** 2
        total += max(0.0, top - (y - shape.half_height)) ** 2
        total += max(0.0, (y + shape.half_height) - bottom) ** 2
    return total


def _anneal_macro_centers(
    state,
    level: CoarseningLevel,
    hyperedges: tuple[ImplementationHyperedge, ...],
    reference_positions: dict[int, Position],
    *,
    target_scale: float,
    iterations: int,
    seed: int,
) -> dict[int, Position]:
    centers = _macro_centers(level, reference_positions)
    shapes = _macro_shapes(state, level)
    movable = {index for index, macro in enumerate(level.macros) if not macro.fixed}
    movable_center = incremental._centroid([centers[index] for index in movable])
    fixed = set(centers) - movable

    left = min(centers[index][0] - shapes[index].half_width for index in movable)
    right = max(centers[index][0] + shapes[index].half_width for index in movable)
    top = min(centers[index][1] - shapes[index].half_height for index in movable)
    bottom = max(centers[index][1] + shapes[index].half_height for index in movable)
    half_width = (right - left) * target_scale / 2.0
    half_height = (bottom - top) * target_scale / 2.0
    envelope = (
        movable_center[0] - half_width,
        movable_center[0] + half_width,
        movable_center[1] - half_height,
        movable_center[1] + half_height,
    )

    # Start with the same global contraction that previously gave a small Pareto-safe improvement;
    # annealing is responsible for repairing connectivity and macro collisions from there.
    for index in movable:
        x, y = centers[index]
        centers[index] = (
            movable_center[0] + (x - movable_center[0]) * target_scale,
            movable_center[1] + (y - movable_center[1]) * target_scale,
        )

    baseline_hpwl = max(1.0, _macro_hpwl(level, hyperedges, centers))
    macro_area = sum(
        4.0 * shapes[index].half_width * shapes[index].half_height for index in movable
    )

    def energy(candidate: dict[int, Position]) -> float:
        hpwl = _macro_hpwl(level, hyperedges, candidate) / baseline_hpwl
        overlap = _overlap_area(candidate, shapes, movable) / max(1.0, macro_area)
        overflow = _envelope_overflow(candidate, shapes, movable, envelope) / max(
            1.0, (2.0 * half_width) ** 2 + (2.0 * half_height) ** 2
        )
        return hpwl + 80.0 * overlap + 120.0 * overflow

    rng = Random(seed)
    current_energy = energy(centers)
    best = dict(centers)
    best_energy = current_energy
    movable_list = sorted(movable)
    neighbor_macros: dict[int, set[int]] = {index: set() for index in movable}
    owner = _macro_owner(level)
    for edge in hyperedges:
        touched = {owner[item] for item in edge.members}
        for left_index in touched:
            if left_index not in movable:
                continue
            neighbor_macros[left_index].update(touched - {left_index})

    span = max(2.0 * half_width, 2.0 * half_height, 1.0)
    for step in range(iterations):
        progress = step / max(1, iterations - 1)
        temperature = max(0.002, 0.20 * (0.01**progress))
        index = movable_list[rng.randrange(len(movable_list))]
        old = centers[index]
        roll = rng.random()
        if roll < 0.60 and neighbor_macros[index]:
            target = incremental._centroid(
                [centers[neighbor] for neighbor in neighbor_macros[index]]
            )
            noise = span * (0.12 * (1.0 - progress) + 0.01)
            proposed = (
                target[0] + rng.uniform(-noise, noise),
                target[1] + rng.uniform(-noise, noise),
            )
        elif roll < 0.90:
            noise = span * (0.10 * (1.0 - progress) + 0.005)
            proposed = (
                old[0] + rng.uniform(-noise, noise),
                old[1] + rng.uniform(-noise, noise),
            )
        else:
            proposed = (
                rng.uniform(envelope[0], envelope[1]),
                rng.uniform(envelope[2], envelope[3]),
            )

        centers[index] = proposed
        candidate_energy = energy(centers)
        delta = candidate_energy - current_energy
        if delta <= 0.0 or rng.random() < exp(-delta / temperature):
            current_energy = candidate_energy
            if candidate_energy < best_energy:
                best_energy = candidate_energy
                best = dict(centers)
        else:
            centers[index] = old

    # Fixed macros are never proposed; assert the contract explicitly in the probe.
    for index in fixed:
        assert best[index] == _macro_centers(level, reference_positions)[index]
    return best


def _reference_from_macro_centers(
    level: CoarseningLevel,
    centers: dict[int, Position],
    fixed_positions: dict[int, Position],
) -> dict[int, Position]:
    result = dict(fixed_positions)
    for macro_index, macro in enumerate(level.macros):
        if macro.fixed:
            continue
        for entity_id in macro.members:
            result[entity_id] = centers[macro_index]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-scales", default="0.65,0.75,0.85")
    parser.add_argument("--macro-iterations", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    scales = tuple(float(value) for value in args.target_scales.split(","))

    started = monotonic()
    result, problem, state = _build_seed()
    grid = layout_optimizer._lattice_grid(problem.lattice)
    hierarchy = build_multilevel_hierarchy(
        result.physical_circuit,
        fixed_entities=frozenset(problem.fixed_positions),
        target_macros=32,
    )
    level = hierarchy.levels[-1]
    flat_positions = layout_optimizer._coarse_implementation_positions(state, grid)
    if flat_positions is None:
        raise RuntimeError("flat coarse legalizer failed")

    report = {
        "flat_coarse": _implementation_geometry(state, flat_positions, hierarchy.hyperedges),
        "macro_count": len(level.macros),
        "target_scales": {},
    }
    fixed_positions = {
        entity_id: state.positions[entity_id] for entity_id in state.fixed_objects
    }
    for scale in scales:
        scale_started = monotonic()
        centers = _anneal_macro_centers(
            state,
            level,
            hierarchy.hyperedges,
            flat_positions,
            target_scale=scale,
            iterations=args.macro_iterations,
            seed=args.seed,
        )
        synthetic_reference = _reference_from_macro_centers(level, centers, fixed_positions)
        unpacked = _positions_from_flat_macro_zoom(
            state,
            grid,
            hierarchy,
            synthetic_reference,
            zoom_scale=1.0,
        )
        report["target_scales"][str(scale)] = {
            "runtime_seconds": monotonic() - scale_started,
            "geometry": None if unpacked is None else _implementation_geometry(
                state, unpacked, hierarchy.hyperedges
            ),
        }
    report["total_runtime_seconds"] = monotonic() - started
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
