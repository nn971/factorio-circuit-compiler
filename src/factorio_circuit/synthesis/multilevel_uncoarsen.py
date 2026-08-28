"""Hierarchical expansion from optimized coarse macros to implementation-level targets.

C6 preserves the multilevel abstraction while walking the C2 hierarchy back toward singleton
implementation entities. Each finer macro is assigned to its unique coarser parent by membership.
Pairwise children are split inside the optimized parent region before deterministic legalization,
so uncoarsening preserves coarse locality rather than making every sibling compete for one center.

Packing slack also decreases toward the finest level. The coarsest expansion retains configurable
macro slack, while singleton macros use their real implementation footprints by default. Relay
topology remains absent throughout C6; the resulting complete legal implementation placement is
intended to be handed to the C5 transactional rerouter.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from math import ceil, sqrt

from factorio_circuit.ir.physical import ConstantCombinator, PhysicalCircuit
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis import joint_layout as exact
from factorio_circuit.synthesis import layout_optimizer, multilevel_zoom
from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.multilevel import CoarseningLevel, MultilevelHierarchy
from factorio_circuit.synthesis.multilevel_anneal import (
    MacroAnnealOptions,
    MacroAnnealStats,
    anneal_macro_geometry,
)
from factorio_circuit.synthesis.multilevel_zoom import (
    MacroGeometry,
    MacroPlacementMetrics,
    build_macro_geometry,
    macro_placement_metrics,
    validate_macro_placement,
)
from factorio_circuit.synthesis.placement import Position

_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class HierarchicalUncoarsenOptions:
    """Bounded controls for coarse-to-fine expansion and refinement.

    ``target_density`` is the packing density at the first expansion below the coarsest level.
    Density increases linearly toward ``finest_density`` so rounding slack does not survive into
    singleton implementation macros.
    """

    target_density: float = 0.80
    finest_density: float = 1.0
    proposals_per_level: int = 512
    random_seed: int = 0
    max_area_factor: float = 1.06
    local_search_radius: int = 2
    final_site_search_radius: int = 48


@dataclass(frozen=True, slots=True)
class UncoarsenLevelResult:
    """Measurements for one expansion/refinement step."""

    level_index: int
    macro_count: int
    target_density: float
    expanded: MacroPlacementMetrics
    refined: MacroPlacementMetrics
    accepted_zoom_scale: float | None
    anneal_stats: MacroAnnealStats


@dataclass(frozen=True, slots=True)
class HierarchicalUncoarsenResult:
    """Finest singleton macro geometry plus per-level coarse measurements."""

    geometry: MacroGeometry
    levels: tuple[UncoarsenLevelResult, ...]


def child_parent_indices(
    coarse_level: CoarseningLevel,
    finer_level: CoarseningLevel,
) -> tuple[int, ...]:
    """Map every finer macro to the unique coarser macro containing all its members."""

    owner = {
        entity_id: macro_index
        for macro_index, macro in enumerate(coarse_level.macros)
        for entity_id in macro.members
    }
    result: list[int] = []
    for child_index, child in enumerate(finer_level.macros):
        parents = {owner[entity_id] for entity_id in child.members if entity_id in owner}
        if not parents:
            raise ValueError(f"finer macro {child_index} has no parent in the coarser level")
        if len(parents) != 1:
            raise ValueError(
                f"finer macro {child_index} crosses coarse parents {sorted(parents)}"
            )
        parent = next(iter(parents))
        if not set(child.members) <= set(coarse_level.macros[parent].members):
            raise ValueError(f"finer macro {child_index} is not contained in parent {parent}")
        result.append(parent)
    return tuple(result)


def _default_macro_legalization_radius(geometry: MacroGeometry) -> int:
    footprint = sum(4.0 * half[0] * half[1] for half in geometry.half_extents)
    return max(8, ceil(sqrt(max(1.0, footprint))))


def _pair_orientation(
    parent_half: tuple[float, float],
    left_half: tuple[float, float],
    right_half: tuple[float, float],
) -> int:
    """Choose x=0 or y=1 for the tighter sibling split inside one parent box."""

    parent_width = max(2.0 * parent_half[0], _EPSILON)
    parent_height = max(2.0 * parent_half[1], _EPSILON)
    horizontal_width = 2.0 * (left_half[0] + right_half[0])
    horizontal_height = 2.0 * max(left_half[1], right_half[1])
    vertical_width = 2.0 * max(left_half[0], right_half[0])
    vertical_height = 2.0 * (left_half[1] + right_half[1])
    horizontal_pressure = max(1.0, horizontal_width / parent_width) * max(
        1.0, horizontal_height / parent_height
    )
    vertical_pressure = max(1.0, vertical_width / parent_width) * max(
        1.0, vertical_height / parent_height
    )
    if horizontal_pressure < vertical_pressure - _EPSILON:
        return 0
    if vertical_pressure < horizontal_pressure - _EPSILON:
        return 1
    return 0 if parent_width >= parent_height else 1


def _child_targets(
    prototype: MacroGeometry,
    coarse_geometry: MacroGeometry,
    parents: tuple[int, ...],
) -> tuple[Position, ...]:
    """Split at most two children around each optimized parent center."""

    by_parent: dict[int, list[int]] = defaultdict(list)
    for child_index, parent_index in enumerate(parents):
        by_parent[parent_index].append(child_index)

    targets = list(prototype.centers)
    for parent_index, children in sorted(by_parent.items()):
        ordered = sorted(children, key=lambda index: prototype.level.macros[index].members)
        movable = [index for index in ordered if not prototype.level.macros[index].fixed]
        if not movable:
            continue
        if len(movable) == 1:
            targets[movable[0]] = coarse_geometry.centers[parent_index]
            continue
        if len(movable) != 2:
            raise ValueError(
                "hierarchical expansion expected pairwise refinement but parent "
                f"{parent_index} has {len(movable)} movable children"
            )

        left, right = movable
        parent = coarse_geometry.centers[parent_index]
        left_half = prototype.half_extents[left]
        right_half = prototype.half_extents[right]
        axis = _pair_orientation(
            coarse_geometry.half_extents[parent_index],
            left_half,
            right_half,
        )
        if axis == 0:
            targets[left] = (parent[0] - right_half[0], parent[1])
            targets[right] = (parent[0] + left_half[0], parent[1])
        else:
            targets[left] = (parent[0], parent[1] - right_half[1])
            targets[right] = (parent[0], parent[1] + left_half[1])
    return tuple(targets)


def expand_macro_level(
    circuit: PhysicalCircuit,
    seed_positions: Mapping[int, Position],
    coarse_geometry: MacroGeometry,
    finer_level: CoarseningLevel,
    *,
    target_density: float = 0.80,
    max_legalization_radius: int | None = None,
) -> MacroGeometry:
    """Expand one pairwise coarse partition while preserving optimized parent locality."""

    prototype = build_macro_geometry(
        circuit,
        seed_positions,
        finer_level,
        target_density=target_density,
    )
    parents = child_parent_indices(coarse_geometry.level, finer_level)
    targets = _child_targets(prototype, coarse_geometry, parents)
    radius = max_legalization_radius
    if radius is None:
        radius = _default_macro_legalization_radius(prototype)
    if radius < 0:
        raise ValueError("max_legalization_radius must be non-negative")

    centers, failure = multilevel_zoom._legalize_targets(
        prototype,
        targets,
        max_radius=radius,
    )
    if centers is None:
        raise ValueError(f"hierarchical expansion could not legalize child macros: {failure}")
    result = replace(prototype, centers=centers)
    validate_macro_placement(result)
    return result


def _level_density(
    options: HierarchicalUncoarsenOptions,
    level_index: int,
    coarsest_finer_index: int,
) -> float:
    if coarsest_finer_index <= 0:
        return options.finest_density
    progress = (coarsest_finer_index - level_index) / coarsest_finer_index
    return options.target_density + progress * (options.finest_density - options.target_density)


def hierarchical_uncoarsen(
    circuit: PhysicalCircuit,
    seed_positions: Mapping[int, Position],
    hierarchy: MultilevelHierarchy,
    coarse_geometry: MacroGeometry,
    *,
    options: HierarchicalUncoarsenOptions | None = None,
) -> HierarchicalUncoarsenResult:
    """Walk every hierarchy level back to singleton macros with bounded refinement."""

    if options is None:
        options = HierarchicalUncoarsenOptions()
    if not 0.0 < options.target_density <= 1.0:
        raise ValueError("target_density must be in (0, 1]")
    if not options.target_density <= options.finest_density <= 1.0:
        raise ValueError("finest_density must be in [target_density, 1]")
    if options.proposals_per_level < 0:
        raise ValueError("proposals_per_level must be non-negative")
    if options.max_area_factor < 1.0:
        raise ValueError("max_area_factor must be at least 1")
    if options.local_search_radius < 0:
        raise ValueError("local_search_radius must be non-negative")
    if options.final_site_search_radius < 0:
        raise ValueError("final_site_search_radius must be non-negative")
    if not hierarchy.levels:
        raise ValueError("multilevel hierarchy must contain at least one level")
    if coarse_geometry.level != hierarchy.levels[-1]:
        raise ValueError("coarse geometry must correspond to the hierarchy's coarsest level")

    current = coarse_geometry
    rows: list[UncoarsenLevelResult] = []
    coarsest_finer_index = len(hierarchy.levels) - 2
    for level_index in range(coarsest_finer_index, -1, -1):
        finer = hierarchy.levels[level_index]
        density = _level_density(options, level_index, coarsest_finer_index)
        expanded = expand_macro_level(
            circuit,
            seed_positions,
            current,
            finer,
            target_density=density,
        )
        expanded_metrics = macro_placement_metrics(expanded, hierarchy.hyperedges)

        # Do not force an area-only zoom after every split. C4 annealing already proposes coherent
        # zooms transactionally and retains the expanded placement when their combined net/area
        # objective is worse.
        annealed = anneal_macro_geometry(
            expanded,
            hierarchy.hyperedges,
            options=MacroAnnealOptions(
                proposals=options.proposals_per_level,
                random_seed=options.random_seed + level_index,
                max_area_factor=options.max_area_factor,
                local_search_radius=options.local_search_radius,
            ),
        )
        current = annealed.geometry
        rows.append(
            UncoarsenLevelResult(
                level_index,
                len(finer.macros),
                density,
                expanded_metrics,
                annealed.after,
                None,
                annealed.stats,
            )
        )

    if any(len(macro.members) != 1 for macro in current.level.macros):
        raise ValueError("finest hierarchy level is not a singleton implementation partition")
    return HierarchicalUncoarsenResult(current, tuple(rows))


def _site_offsets(radius: int) -> tuple[Position, ...]:
    offsets: list[Position] = []
    for shell in range(2 * radius + 1):
        ring = [
            (dx / 2.0, dy / 2.0)
            for dx in range(-shell, shell + 1)
            for dy in range(-shell, shell + 1)
            if max(abs(dx), abs(dy)) == shell
        ]
        ring.sort(key=lambda item: (item[0] * item[0] + item[1] * item[1], item))
        offsets.extend(ring)
    return tuple(offsets)


def _candidate_hits_forbidden_area(
    position: Position,
    half: tuple[float, float],
    areas: tuple[base_placement.RelayForbiddenArea, ...],
) -> bool:
    x, y = position
    half_x, half_y = half
    return any(
        x + half_x > left + _EPSILON
        and x - half_x < right - _EPSILON
        and y + half_y > top + _EPSILON
        and y - half_y < bottom - _EPSILON
        for left, right, top, bottom in areas
    )


def legalize_singleton_implementation_targets(
    problem: layout_optimizer.LayoutOptimizationProblem,
    geometry: MacroGeometry,
    *,
    search_radius: int = 48,
) -> dict[int, Position]:
    """Project singleton macro centers onto exact legal implementation sites."""

    if search_radius < 0:
        raise ValueError("search_radius must be non-negative")
    circuit = problem.layout.circuit
    entity_ids = {entity.id for entity in circuit.entities}
    if any(len(macro.members) != 1 for macro in geometry.level.macros):
        raise ValueError("implementation projection requires singleton macros")
    macro_ids = {macro.members[0] for macro in geometry.level.macros}
    if macro_ids != entity_ids:
        raise ValueError("singleton macro partition does not match implementation entities")

    target_by_id = {
        macro.members[0]: geometry.centers[index]
        for index, macro in enumerate(geometry.level.macros)
    }
    baseline = layout_optimizer._validated_embedding(problem).state
    fixed = {
        entity_id: problem.fixed_positions[entity_id]
        for entity_id in problem.fixed_positions
        if entity_id in entity_ids
    }
    placement_state = exact._JointState(
        circuit=baseline.circuit,
        endpoints_by_group=baseline.endpoints_by_group,
        colors_by_group=baseline.colors_by_group,
        positions=dict(fixed),
        relay_positions={},
        relay_groups={},
        safe_span=baseline.safe_span,
        forbidden_areas=baseline.forbidden_areas,
        fixed_objects=baseline.fixed_objects,
    )
    occupancy = incremental._SpatialOccupancy.build(placement_state)
    entities = {entity.id: entity for entity in circuit.entities}
    degree: dict[int, int] = defaultdict(int)
    for connection in circuit.connections:
        degree[connection.source.entity] += 1
        degree[connection.target.entity] += 1

    movable = sorted(
        entity_ids - set(fixed),
        key=lambda entity_id: (
            -(
                4.0
                * base_placement._entity_half_extent(entities[entity_id])[0]
                * base_placement._entity_half_extent(entities[entity_id])[1]
            ),
            -degree[entity_id],
            entity_id,
        ),
    )
    unit_sites = set(problem.lattice.unit_sites)
    wide_sites = set(problem.lattice.wide_sites)
    offsets = _site_offsets(search_radius)
    result = dict(fixed)

    for entity_id in movable:
        entity = entities[entity_id]
        legal_sites = unit_sites if isinstance(entity, ConstantCombinator) else wide_sites
        target = target_by_id[entity_id]
        snapped = (round(2.0 * target[0]) / 2.0, round(2.0 * target[1]) / 2.0)
        half = base_placement._entity_half_extent(entity)
        chosen: Position | None = None
        for offset_x, offset_y in offsets:
            candidate = (snapped[0] + offset_x, snapped[1] + offset_y)
            if candidate not in legal_sites:
                continue
            if _candidate_hits_forbidden_area(
                candidate,
                half,
                problem.lattice.forbidden_areas,
            ):
                continue
            if occupancy.overlaps(entity_id, candidate, ignored=set()):
                continue
            chosen = candidate
            break
        if chosen is None:
            raise ValueError(
                f"no legal implementation site for entity {entity_id} within {search_radius} tiles"
            )
        result[entity_id] = chosen
        placement_state.positions[entity_id] = chosen
        occupancy.add(entity_id, chosen)

    layout_optimizer._validate_object_clearance(
        result,
        {
            entity_id: base_placement._entity_half_extent(entity)
            for entity_id, entity in entities.items()
        },
        problem.lattice.forbidden_areas,
    )
    return result
