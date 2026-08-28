"""Cheap coarse-scale macro refinement for Milestone C4.

The annealer operates only on abstract placement macros. It never routes wires and never consults
relay geometry. Expensive relay rebuilding remains a transactional checkpoint operation for C5.

The energy is normalized against the dense C3 seed and combines occupied bounding area, logical
hypernet HPWL, and a coarse cut-congestion estimate. A hard area-growth bound prevents the annealer
from buying arbitrary wire-length improvements by reopening the compact envelope.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import exp
from random import Random

from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.multilevel import (
    ImplementationHyperedge,
    macro_pair_affinities,
)
from factorio_circuit.synthesis.multilevel_zoom import (
    MacroGeometry,
    MacroPlacementMetrics,
    macro_placement_metrics,
    try_macro_zoom,
    validate_macro_placement,
)
from factorio_circuit.synthesis.placement import Position

_EPSILON = 1e-9
_TRANSLATION_STEPS = (-4.0, -2.0, -1.0, -0.5, 0.5, 1.0, 2.0, 4.0)


@dataclass(frozen=True, slots=True)
class MacroAnnealOptions:
    """Bounded deterministic-by-seed coarse annealing controls."""

    proposals: int = 4096
    random_seed: int = 0
    initial_temperature: float = 0.06
    final_temperature: float = 0.001
    area_weight: float = 1.0
    hpwl_weight: float = 1.0
    congestion_weight: float = 0.25
    max_area_factor: float = 1.08
    local_search_radius: int = 5


@dataclass(frozen=True, slots=True)
class MacroAnnealEnergy:
    """Normalized objective components and scalar energy."""

    area_ratio: float
    hpwl_ratio: float
    congestion_ratio: float
    value: float


@dataclass(frozen=True, slots=True)
class MacroAnnealStats:
    """Work counters for one bounded coarse annealing run."""

    proposals: int
    legal_proposals: int
    accepted_proposals: int
    accepted_uphill: int
    best_updates: int


@dataclass(frozen=True, slots=True)
class MacroAnnealResult:
    """Best coarse macro geometry observed during one annealing run."""

    geometry: MacroGeometry
    before: MacroPlacementMetrics
    after: MacroPlacementMetrics
    before_congestion: float
    after_congestion: float
    before_energy: MacroAnnealEnergy
    after_energy: MacroAnnealEnergy
    stats: MacroAnnealStats


def _project_hyperedges(
    geometry: MacroGeometry,
    hyperedges: tuple[ImplementationHyperedge, ...],
) -> tuple[tuple[int, ...], ...]:
    owner = {
        entity_id: macro_index
        for macro_index, macro in enumerate(geometry.level.macros)
        for entity_id in macro.members
    }
    projected = []
    for edge in hyperedges:
        touched = tuple(sorted({owner[entity_id] for entity_id in edge.members}))
        if len(touched) > 1:
            projected.append(touched)
    return tuple(projected)


def _bounding_box(geometry: MacroGeometry) -> tuple[float, float, float]:
    if not geometry.centers:
        return (0.0, 0.0, 0.0)
    left = min(
        center[0] - half[0]
        for center, half in zip(geometry.centers, geometry.half_extents, strict=True)
    )
    right = max(
        center[0] + half[0]
        for center, half in zip(geometry.centers, geometry.half_extents, strict=True)
    )
    top = min(
        center[1] - half[1]
        for center, half in zip(geometry.centers, geometry.half_extents, strict=True)
    )
    bottom = max(
        center[1] + half[1]
        for center, half in zip(geometry.centers, geometry.half_extents, strict=True)
    )
    width = right - left
    height = bottom - top
    return (width, height, width * height)


def _projected_hpwl(
    centers: tuple[Position, ...],
    projected_edges: tuple[tuple[int, ...], ...],
) -> float:
    hpwl = 0.0
    for touched in projected_edges:
        xs = [centers[index][0] for index in touched]
        ys = [centers[index][1] for index in touched]
        hpwl += max(xs) - min(xs) + max(ys) - min(ys)
    return hpwl


def _axis_cut_congestion(
    centers: tuple[Position, ...],
    projected_edges: tuple[tuple[int, ...], ...],
    *,
    axis: int,
    cross_section: float,
) -> float:
    coordinates = sorted({center[axis] for center in centers})
    if len(coordinates) <= 1:
        return 0.0
    rank = {coordinate: index for index, coordinate in enumerate(coordinates)}
    difference = [0.0 for _ in coordinates]
    for touched in projected_edges:
        ranks = [rank[centers[index][axis]] for index in touched]
        low = min(ranks)
        high = max(ranks)
        if low == high:
            continue
        difference[low] += 1.0
        difference[high] -= 1.0

    running = 0.0
    squared_demand = 0.0
    for delta in difference[:-1]:
        running += delta
        squared_demand += running * running
    return squared_demand / max(cross_section, 1.0)


def coarse_cut_congestion(
    geometry: MacroGeometry,
    hyperedges: tuple[ImplementationHyperedge, ...],
) -> float:
    """Estimate channel pressure from squared logical-net demand across x/y cuts."""

    projected = _project_hyperedges(geometry, hyperedges)
    width, height, _area = _bounding_box(geometry)
    return _axis_cut_congestion(
        geometry.centers,
        projected,
        axis=0,
        cross_section=height,
    ) + _axis_cut_congestion(
        geometry.centers,
        projected,
        axis=1,
        cross_section=width,
    )


def _raw_components(
    geometry: MacroGeometry,
    projected_edges: tuple[tuple[int, ...], ...],
) -> tuple[float, float, float]:
    width, height, area = _bounding_box(geometry)
    hpwl = _projected_hpwl(geometry.centers, projected_edges)
    congestion = _axis_cut_congestion(
        geometry.centers,
        projected_edges,
        axis=0,
        cross_section=height,
    ) + _axis_cut_congestion(
        geometry.centers,
        projected_edges,
        axis=1,
        cross_section=width,
    )
    return (area, hpwl, congestion)


def _normalized_energy(
    raw: tuple[float, float, float],
    baseline: tuple[float, float, float],
    options: MacroAnnealOptions,
) -> MacroAnnealEnergy:
    area_ratio = raw[0] / max(baseline[0], _EPSILON)
    hpwl_ratio = raw[1] / max(baseline[1], _EPSILON)
    congestion_ratio = raw[2] / max(baseline[2], _EPSILON)
    value = (
        options.area_weight * area_ratio
        + options.hpwl_weight * hpwl_ratio
        + options.congestion_weight * congestion_ratio
    )
    return MacroAnnealEnergy(area_ratio, hpwl_ratio, congestion_ratio, value)


def _snap_half(value: float) -> float:
    return round(2.0 * value) / 2.0


def _local_offsets(radius: int) -> tuple[Position, ...]:
    offsets = [
        (dx / 2.0, dy / 2.0)
        for dx in range(-2 * radius, 2 * radius + 1)
        for dy in range(-2 * radius, 2 * radius + 1)
    ]
    offsets.sort(key=lambda item: (item[0] * item[0] + item[1] * item[1], item))
    return tuple(offsets)


def _single_position_is_legal(
    geometry: MacroGeometry,
    index: int,
    position: Position,
) -> bool:
    for other_index, other_position in enumerate(geometry.centers):
        if other_index == index:
            continue
        if base_placement._boxes_overlap(
            position,
            geometry.half_extents[index],
            other_position,
            geometry.half_extents[other_index],
        ):
            return False
    return True


def _move_single_near(
    geometry: MacroGeometry,
    index: int,
    target: Position,
    offsets: tuple[Position, ...],
) -> MacroGeometry | None:
    snapped = (_snap_half(target[0]), _snap_half(target[1]))
    for offset_x, offset_y in offsets:
        candidate = (snapped[0] + offset_x, snapped[1] + offset_y)
        if not _single_position_is_legal(geometry, index, candidate):
            continue
        centers = list(geometry.centers)
        centers[index] = candidate
        return replace(geometry, centers=tuple(centers))
    return None


def _weighted_neighbor_target(
    geometry: MacroGeometry,
    index: int,
    neighbors: tuple[tuple[int, float], ...],
) -> Position | None:
    if not neighbors:
        return None
    total = sum(weight for _other, weight in neighbors)
    if total <= _EPSILON:
        return None
    return (
        sum(geometry.centers[other][0] * weight for other, weight in neighbors) / total,
        sum(geometry.centers[other][1] * weight for other, weight in neighbors) / total,
    )


def _candidate_geometry(
    geometry: MacroGeometry,
    hyperedges: tuple[ImplementationHyperedge, ...],
    affinities: dict[tuple[int, int], float],
    movable: tuple[int, ...],
    related_pairs: tuple[tuple[int, int], ...],
    offsets: tuple[Position, ...],
    rng: Random,
) -> MacroGeometry | None:
    roll = rng.random()

    if roll < 0.36:
        index = rng.choice(movable)
        dx = rng.choice(_TRANSLATION_STEPS)
        dy = rng.choice(_TRANSLATION_STEPS)
        current = geometry.centers[index]
        return _move_single_near(
            geometry,
            index,
            (current[0] + dx, current[1] + dy),
            offsets,
        )

    if roll < 0.66:
        index = rng.choice(movable)
        neighbors = tuple(
            sorted(
                (
                    (right if left == index else left, weight)
                    for (left, right), weight in affinities.items()
                    if left == index or right == index
                ),
                key=lambda item: (-item[1], item[0]),
            )
        )
        target = _weighted_neighbor_target(geometry, index, neighbors)
        if target is None:
            return None
        current = geometry.centers[index]
        fraction = rng.uniform(0.35, 0.85)
        jitter_x = rng.choice((-2.0, -1.0, 0.0, 1.0, 2.0))
        jitter_y = rng.choice((-2.0, -1.0, 0.0, 1.0, 2.0))
        migration_target = (
            current[0] + fraction * (target[0] - current[0]) + jitter_x,
            current[1] + fraction * (target[1] - current[1]) + jitter_y,
        )
        return _move_single_near(geometry, index, migration_target, offsets)

    if roll < 0.80:
        if len(movable) < 2:
            return None
        left, right = rng.sample(movable, 2)
        centers = list(geometry.centers)
        centers[left], centers[right] = centers[right], centers[left]
        candidate = replace(geometry, centers=tuple(centers))
        try:
            validate_macro_placement(candidate)
        except ValueError:
            return None
        return candidate

    if roll < 0.94:
        if not related_pairs:
            return None
        left, right = rng.choice(related_pairs)
        dx = rng.choice(_TRANSLATION_STEPS)
        dy = rng.choice(_TRANSLATION_STEPS)
        centers = list(geometry.centers)
        for index in (left, right):
            centers[index] = (
                _snap_half(centers[index][0] + dx),
                _snap_half(centers[index][1] + dy),
            )
        candidate = replace(geometry, centers=tuple(centers))
        try:
            validate_macro_placement(candidate)
        except ValueError:
            return None
        return candidate

    scale = rng.choice((0.92, 0.95, 0.97))
    zoom_candidate, _failure = try_macro_zoom(geometry, scale=scale)
    return zoom_candidate


def anneal_macro_geometry(
    geometry: MacroGeometry,
    hyperedges: tuple[ImplementationHyperedge, ...],
    *,
    options: MacroAnnealOptions | None = None,
) -> MacroAnnealResult:
    """Refine one legal dense macro placement with cheap global-net objectives."""

    if options is None:
        options = MacroAnnealOptions()
    if options.proposals < 0:
        raise ValueError("proposals must be non-negative")
    if options.initial_temperature <= 0.0 or options.final_temperature <= 0.0:
        raise ValueError("annealing temperatures must be positive")
    if options.max_area_factor < 1.0:
        raise ValueError("max_area_factor must be at least 1")
    if options.local_search_radius < 0:
        raise ValueError("local_search_radius must be non-negative")
    if any(
        weight < 0.0
        for weight in (options.area_weight, options.hpwl_weight, options.congestion_weight)
    ):
        raise ValueError("annealing objective weights must be non-negative")

    validate_macro_placement(geometry)
    projected_edges = _project_hyperedges(geometry, hyperedges)
    baseline = _raw_components(geometry, projected_edges)
    before = macro_placement_metrics(geometry, hyperedges)
    before_congestion = baseline[2]
    before_energy = _normalized_energy(baseline, baseline, options)

    movable = tuple(index for index, macro in enumerate(geometry.level.macros) if not macro.fixed)
    if not movable or options.proposals == 0:
        stats = MacroAnnealStats(options.proposals, 0, 0, 0, 0)
        return MacroAnnealResult(
            geometry,
            before,
            before,
            before_congestion,
            before_congestion,
            before_energy,
            before_energy,
            stats,
        )

    affinities = macro_pair_affinities(geometry.level, hyperedges)
    related_pairs = tuple(
        sorted(
            (pair for pair in affinities if pair[0] in movable and pair[1] in movable),
            key=lambda pair: (-affinities[pair], pair),
        )
    )
    offsets = _local_offsets(options.local_search_radius)
    rng = Random(options.random_seed)

    current = geometry
    current_raw = baseline
    current_energy = before_energy
    best = geometry
    best_raw = baseline
    best_energy = before_energy
    legal_proposals = 0
    accepted_proposals = 0
    accepted_uphill = 0
    best_updates = 0
    maximum_area = baseline[0] * options.max_area_factor

    for proposal in range(options.proposals):
        candidate = _candidate_geometry(
            current,
            hyperedges,
            affinities,
            movable,
            related_pairs,
            offsets,
            rng,
        )
        if candidate is None:
            continue
        candidate_raw = _raw_components(candidate, projected_edges)
        if candidate_raw[0] > maximum_area + _EPSILON:
            continue
        legal_proposals += 1
        candidate_energy = _normalized_energy(candidate_raw, baseline, options)
        fraction = 0.0 if options.proposals <= 1 else proposal / (options.proposals - 1)
        temperature = (
            options.initial_temperature
            * (options.final_temperature / options.initial_temperature) ** fraction
        )
        delta = candidate_energy.value - current_energy.value
        accepted = delta <= 0.0 or rng.random() < exp(-delta / temperature)
        if not accepted:
            continue
        if delta > 0.0:
            accepted_uphill += 1
        current = candidate
        current_raw = candidate_raw
        current_energy = candidate_energy
        accepted_proposals += 1
        if current_energy.value < best_energy.value - _EPSILON:
            best = current
            best_raw = current_raw
            best_energy = current_energy
            best_updates += 1

    validate_macro_placement(best)
    after = macro_placement_metrics(best, hyperedges)
    stats = MacroAnnealStats(
        options.proposals,
        legal_proposals,
        accepted_proposals,
        accepted_uphill,
        best_updates,
    )
    return MacroAnnealResult(
        best,
        before,
        after,
        before_congestion,
        best_raw[2],
        before_energy,
        best_energy,
        stats,
    )
