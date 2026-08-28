"""Relay-blind coarse macro geometry and global zoom legalization.

This module is the Milestone C3 proof-of-concept layer between implementation-only multilevel
coarsening and eventual transactional physical rerouting. It deliberately does *not* move routed
relays or preserve the failproof relay scaffold. Instead, a coarsening level is represented by
compact abstract rectangular macro footprints derived only from implementation entity sizes.
Existing implementation coordinates provide initial macro centers; routed relay geometry never
participates.

A global zoom contracts movable macro centers toward a common center, leaves fixed singleton macros
exact, then legalizes the abstract macro rectangles with a bounded deterministic nearest-target
search. The result is still an abstract coarse placement: later uncoarsening expands macros back to
implementation entities, while transactional rerouting rebuilds the relay topology and validates the
physical artifact exactly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from math import ceil, sqrt

from factorio_circuit.ir.physical import PhysicalCircuit
from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.multilevel import CoarseningLevel, ImplementationHyperedge
from factorio_circuit.synthesis.placement import Position

_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class MacroGeometry:
    """One coarse placement level with compact abstract footprints and current centers."""

    level: CoarseningLevel
    centers: tuple[Position, ...]
    half_extents: tuple[tuple[float, float], ...]
    implementation_area: float


@dataclass(frozen=True, slots=True)
class MacroPlacementMetrics:
    """Cheap coarse metrics used before any exact relay routing exists."""

    bounding_width: float
    bounding_height: float
    bounding_area: float
    implementation_area: float
    implementation_occupancy: float
    hypernet_hpwl: float

    @property
    def objective(self) -> tuple[float, float]:
        return (self.bounding_area, self.hypernet_hpwl)


@dataclass(frozen=True, slots=True)
class MacroZoomResult:
    """Result of one aggressive-to-gentle coarse zoom line search."""

    geometry: MacroGeometry
    before: MacroPlacementMetrics
    after: MacroPlacementMetrics
    accepted_scale: float | None
    rejected_scales: tuple[tuple[float, str], ...]


def _centroid(points: list[Position]) -> Position:
    if not points:
        return (0.0, 0.0)
    return (
        sum(x for x, _y in points) / len(points),
        sum(y for _x, y in points) / len(points),
    )


def _snap_half(value: float) -> float:
    return round(2.0 * value) / 2.0


def _packed_macro_half_extent(
    circuit: PhysicalCircuit,
    members: tuple[int, ...],
    *,
    target_density: float,
) -> tuple[tuple[float, float], float]:
    entities = {entity.id: entity for entity in circuit.entities}
    widths: list[float] = []
    heights: list[float] = []
    area = 0.0
    for entity_id in members:
        try:
            half_x, half_y = base_placement._entity_half_extent(entities[entity_id])
        except KeyError as exc:
            raise ValueError(f"macro contains unknown implementation entity {entity_id}") from exc
        width = 2.0 * half_x
        height = 2.0 * half_y
        widths.append(width)
        heights.append(height)
        area += width * height

    if not widths:
        raise ValueError("placement macros must contain at least one implementation entity")
    target_area = area / target_density
    width = max(max(widths), float(ceil(sqrt(target_area))))
    height = max(max(heights), float(ceil(target_area / width)))
    while width * height + _EPSILON < target_area:
        height += 1.0
    return (0.5 * width, 0.5 * height), area


def build_macro_geometry(
    circuit: PhysicalCircuit,
    positions: Mapping[int, Position],
    level: CoarseningLevel,
    *,
    target_density: float = 0.85,
) -> MacroGeometry:
    """Construct compact relay-blind macro rectangles for one hierarchy level.

    Macro footprint area is derived from implementation footprint area divided by
    ``target_density``. The current failproof seed contributes only the initial macro center.
    Fixed macros are required to be singletons and keep the exact footprint of their entity.
    """

    if not 0.0 < target_density <= 1.0:
        raise ValueError("target_density must be in (0, 1]")
    implementation_ids = {entity.id for entity in circuit.entities}
    missing = implementation_ids - positions.keys()
    if missing:
        raise ValueError(f"positions are missing implementation entities: {sorted(missing)}")

    seen: set[int] = set()
    centers: list[Position] = []
    half_extents: list[tuple[float, float]] = []
    total_area = 0.0
    entities = {entity.id: entity for entity in circuit.entities}
    for macro in level.macros:
        duplicates = seen.intersection(macro.members)
        if duplicates:
            rendered = sorted(duplicates)
            raise ValueError(f"coarsening level repeats implementation entities: {rendered}")
        seen.update(macro.members)
        unknown = set(macro.members) - implementation_ids
        if unknown:
            raise ValueError(f"macro contains unknown implementation entities: {sorted(unknown)}")

        if macro.fixed:
            if len(macro.members) != 1:
                raise ValueError("fixed placement macros must remain singleton macros")
            entity_id = macro.members[0]
            half = base_placement._entity_half_extent(entities[entity_id])
            area = 4.0 * half[0] * half[1]
            center = positions[entity_id]
        else:
            half, area = _packed_macro_half_extent(
                circuit,
                macro.members,
                target_density=target_density,
            )
            center = _centroid([positions[entity_id] for entity_id in macro.members])
        centers.append(center)
        half_extents.append(half)
        total_area += area

    if seen != implementation_ids:
        missing_from_level = sorted(implementation_ids - seen)
        raise ValueError(
            "coarsening level does not cover all implementation entities: "
            f"{missing_from_level}"
        )
    return MacroGeometry(level, tuple(centers), tuple(half_extents), total_area)


def _macro_owner(geometry: MacroGeometry) -> dict[int, int]:
    return {
        entity_id: macro_index
        for macro_index, macro in enumerate(geometry.level.macros)
        for entity_id in macro.members
    }


def macro_placement_metrics(
    geometry: MacroGeometry,
    hyperedges: tuple[ImplementationHyperedge, ...],
) -> MacroPlacementMetrics:
    """Measure coarse envelope occupancy and implementation-hypernet HPWL."""

    if not geometry.centers:
        return MacroPlacementMetrics(0.0, 0.0, 0.0, 0.0, 1.0, 0.0)

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
    area = width * height
    occupancy = 1.0 if area <= _EPSILON else geometry.implementation_area / area

    owner = _macro_owner(geometry)
    hpwl = 0.0
    for edge in hyperedges:
        touched = tuple(sorted({owner[entity_id] for entity_id in edge.members}))
        if len(touched) <= 1:
            continue
        xs = [geometry.centers[index][0] for index in touched]
        ys = [geometry.centers[index][1] for index in touched]
        hpwl += (max(xs) - min(xs)) + (max(ys) - min(ys))

    return MacroPlacementMetrics(width, height, area, geometry.implementation_area, occupancy, hpwl)


def _boxes_overlap(
    left_position: Position,
    left_half: tuple[float, float],
    right_position: Position,
    right_half: tuple[float, float],
) -> bool:
    return base_placement._boxes_overlap(
        left_position,
        left_half,
        right_position,
        right_half,
    )


def validate_macro_placement(geometry: MacroGeometry) -> None:
    """Check that legalized abstract macro rectangles are pairwise disjoint."""

    if len(geometry.centers) != len(geometry.level.macros):
        raise ValueError("macro center count does not match coarsening level")
    if len(geometry.half_extents) != len(geometry.level.macros):
        raise ValueError("macro footprint count does not match coarsening level")
    for left in range(len(geometry.centers)):
        for right in range(left + 1, len(geometry.centers)):
            if _boxes_overlap(
                geometry.centers[left],
                geometry.half_extents[left],
                geometry.centers[right],
                geometry.half_extents[right],
            ):
                raise ValueError(f"coarse macros {left} and {right} overlap")


def _zoom_center(geometry: MacroGeometry) -> Position:
    fixed = [
        geometry.centers[index]
        for index, macro in enumerate(geometry.level.macros)
        if macro.fixed
    ]
    return _centroid(fixed if fixed else list(geometry.centers))


def _candidate_offsets(max_radius: int) -> tuple[tuple[int, int], ...]:
    offsets: list[tuple[int, int]] = []
    for radius in range(max_radius + 1):
        ring = [
            (dx, dy)
            for dx in range(-radius, radius + 1)
            for dy in range(-radius, radius + 1)
            if max(abs(dx), abs(dy)) == radius
        ]
        ring.sort(key=lambda item: (item[0] * item[0] + item[1] * item[1], item))
        offsets.extend(ring)
    return tuple(offsets)


def _legalize_targets(
    geometry: MacroGeometry,
    targets: tuple[Position, ...],
    *,
    max_radius: int,
) -> tuple[tuple[Position, ...] | None, str | None]:
    placed: dict[int, Position] = {}
    fixed = [index for index, macro in enumerate(geometry.level.macros) if macro.fixed]
    for index in fixed:
        placed[index] = geometry.centers[index]
    for left_index, left in enumerate(fixed):
        for right in fixed[left_index + 1 :]:
            if _boxes_overlap(
                placed[left],
                geometry.half_extents[left],
                placed[right],
                geometry.half_extents[right],
            ):
                return None, f"fixed coarse macros {left} and {right} overlap"

    center = _zoom_center(geometry)
    movable = sorted(
        (index for index, macro in enumerate(geometry.level.macros) if not macro.fixed),
        key=lambda index: (
            -(4.0 * geometry.half_extents[index][0] * geometry.half_extents[index][1]),
            -(
                (targets[index][0] - center[0]) ** 2
                + (targets[index][1] - center[1]) ** 2
            ),
            geometry.level.macros[index].members,
        ),
    )
    offsets = _candidate_offsets(max_radius)
    for index in movable:
        target = (_snap_half(targets[index][0]), _snap_half(targets[index][1]))
        chosen: Position | None = None
        for dx, dy in offsets:
            candidate = (target[0] + dx, target[1] + dy)
            if all(
                not _boxes_overlap(
                    candidate,
                    geometry.half_extents[index],
                    other_position,
                    geometry.half_extents[other_index],
                )
                for other_index, other_position in placed.items()
            ):
                chosen = candidate
                break
        if chosen is None:
            return (
                None,
                f"no legal center for coarse macro {index} within {max_radius} tiles of its target",
            )
        placed[index] = chosen

    result = tuple(placed[index] for index in range(len(geometry.level.macros)))
    candidate = replace(geometry, centers=result)
    validate_macro_placement(candidate)
    return result, None


def try_macro_zoom(
    geometry: MacroGeometry,
    *,
    scale: float,
    max_legalization_radius: int | None = None,
) -> tuple[MacroGeometry | None, str | None]:
    """Contract macro centers coherently, then legalize compact macro footprints."""

    if not 0.0 < scale < 1.0:
        raise ValueError("macro zoom scale must be in (0, 1)")
    center = _zoom_center(geometry)
    targets = tuple(
        geometry.centers[index]
        if macro.fixed
        else (
            center[0] + scale * (geometry.centers[index][0] - center[0]),
            center[1] + scale * (geometry.centers[index][1] - center[1]),
        )
        for index, macro in enumerate(geometry.level.macros)
    )
    footprint_area = sum(4.0 * half[0] * half[1] for half in geometry.half_extents)
    radius = max_legalization_radius
    if radius is None:
        radius = max(8, ceil(0.85 * sqrt(max(1.0, footprint_area))))
    if radius < 0:
        raise ValueError("max_legalization_radius must be non-negative")

    centers, failure = _legalize_targets(geometry, targets, max_radius=radius)
    if centers is None:
        return None, failure
    return replace(geometry, centers=centers), None


def compact_macro_geometry(
    geometry: MacroGeometry,
    hyperedges: tuple[ImplementationHyperedge, ...],
    *,
    scales: tuple[float, ...] = (0.08, 0.10, 0.12, 0.15, 0.20, 0.30, 0.50, 0.70),
    max_legalization_radius: int | None = None,
) -> MacroZoomResult:
    """Back off an aggressive global zoom until coarse legalization succeeds."""

    if not scales:
        raise ValueError("at least one macro zoom scale is required")
    before = macro_placement_metrics(geometry, hyperedges)
    rejected: list[tuple[float, str]] = []
    for scale in scales:
        candidate, failure = try_macro_zoom(
            geometry,
            scale=scale,
            max_legalization_radius=max_legalization_radius,
        )
        if candidate is None:
            rejected.append((scale, failure or "macro zoom legalization rejected"))
            continue
        after = macro_placement_metrics(candidate, hyperedges)
        if after.bounding_area >= before.bounding_area - _EPSILON:
            rejected.append((scale, "legalized candidate did not contract the coarse envelope"))
            continue
        return MacroZoomResult(candidate, before, after, scale, tuple(rejected))

    return MacroZoomResult(geometry, before, before, None, tuple(rejected))
