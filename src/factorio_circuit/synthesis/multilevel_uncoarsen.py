"""Hierarchical expansion from optimized coarse macros to implementation-level targets.

C6 preserves the multilevel abstraction while walking the C2 hierarchy back toward singleton
implementation entities. Every optimized coarse macro owns a non-overlapping rectangular region.
Because C2 coarsening is pairwise, refinement can subdivide that region into one or two child
regions without sending siblings back through a global placement problem.

The owned region is also allowed to shrink as target density rises toward the finest level. This
removes coarse proxy-box rounding slack without scattering child centers outside their parent.
Relay topology remains absent throughout C6; the resulting singleton centers are projected onto the
real implementation lattice and handed to the C5 transactional rerouter.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from math import sqrt

from factorio_circuit.ir.physical import ConstantCombinator, PhysicalCircuit
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis import joint_layout as exact
from factorio_circuit.synthesis import layout_optimizer
from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.multilevel import (
    CoarseningLevel,
    MultilevelHierarchy,
    PlacementMacro,
)
from factorio_circuit.synthesis.multilevel_anneal import (
    MacroAnnealOptions,
    MacroAnnealStats,
    anneal_macro_geometry,
)
from factorio_circuit.synthesis.multilevel_zoom import (
    MacroGeometry,
    MacroPlacementMetrics,
    macro_placement_metrics,
    validate_macro_placement,
)
from factorio_circuit.synthesis.placement import Position

_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class HierarchicalUncoarsenOptions:
    """Bounded controls for coarse-to-fine expansion and refinement.

    ``target_density`` is the density at the first refinement below the coarsest level. Density
    rises linearly toward ``finest_density``. Parent regions are shrunk only when their current area
    exceeds the implementation area required at the new density.
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
            raise ValueError(f"finer macro {child_index} crosses coarse parents {sorted(parents)}")
        parent = next(iter(parents))
        if not set(child.members) <= set(coarse_level.macros[parent].members):
            raise ValueError(f"finer macro {child_index} is not contained in parent {parent}")
        result.append(parent)
    return tuple(result)


def _macro_implementation_area(circuit: PhysicalCircuit, macro: PlacementMacro) -> float:
    area = 0.0
    for entity_id in macro.members:
        half_x, half_y = base_placement._entity_half_extent(circuit.entity_by_id(entity_id))
        area += 4.0 * half_x * half_y
    return area


def _scaled_parent_half_extent(
    parent_half: tuple[float, float],
    implementation_area: float,
    target_density: float,
) -> tuple[float, float]:
    parent_area = 4.0 * parent_half[0] * parent_half[1]
    if parent_area <= _EPSILON:
        raise ValueError("coarse macro region must have positive area")
    desired_area = implementation_area / target_density
    scale = min(1.0, sqrt(desired_area / parent_area))
    return (parent_half[0] * scale, parent_half[1] * scale)


def _subdivide_parent_region(
    circuit: PhysicalCircuit,
    coarse_geometry: MacroGeometry,
    finer_level: CoarseningLevel,
    parent_index: int,
    child_indices: tuple[int, ...],
    *,
    target_density: float,
) -> tuple[tuple[int, Position, tuple[float, float]], ...]:
    """Return child regions wholly contained in one optimized parent region."""

    parent_macro = coarse_geometry.level.macros[parent_index]
    ordered = tuple(sorted(child_indices, key=lambda index: finer_level.macros[index].members))
    child_members = {
        entity_id
        for child_index in ordered
        for entity_id in finer_level.macros[child_index].members
    }
    if child_members != set(parent_macro.members):
        raise ValueError(f"children do not exactly partition coarse macro {parent_index}")

    parent_center = coarse_geometry.centers[parent_index]
    parent_half = coarse_geometry.half_extents[parent_index]
    if parent_macro.fixed:
        if len(ordered) != 1 or not finer_level.macros[ordered[0]].fixed:
            raise ValueError("fixed coarse macro must remain one fixed child during uncoarsening")
        return ((ordered[0], parent_center, parent_half),)

    if len(ordered) not in {1, 2}:
        raise ValueError(
            "hierarchical expansion expected pairwise refinement but parent "
            f"{parent_index} has {len(ordered)} children"
        )

    child_areas = tuple(
        _macro_implementation_area(circuit, finer_level.macros[index]) for index in ordered
    )
    implementation_area = sum(child_areas)
    scaled_half = _scaled_parent_half_extent(parent_half, implementation_area, target_density)
    if len(ordered) == 1:
        return ((ordered[0], parent_center, scaled_half),)

    left, right = ordered
    left_area, right_area = child_areas
    left_fraction = left_area / implementation_area
    width = 2.0 * scaled_half[0]
    height = 2.0 * scaled_half[1]
    if width >= height:
        left_width = width * left_fraction
        right_width = width - left_width
        left_half = (0.5 * left_width, scaled_half[1])
        right_half = (0.5 * right_width, scaled_half[1])
        left_center = (
            parent_center[0] - scaled_half[0] + left_half[0],
            parent_center[1],
        )
        right_center = (
            parent_center[0] + scaled_half[0] - right_half[0],
            parent_center[1],
        )
    else:
        top_height = height * left_fraction
        bottom_height = height - top_height
        left_half = (scaled_half[0], 0.5 * top_height)
        right_half = (scaled_half[0], 0.5 * bottom_height)
        left_center = (
            parent_center[0],
            parent_center[1] - scaled_half[1] + left_half[1],
        )
        right_center = (
            parent_center[0],
            parent_center[1] + scaled_half[1] - right_half[1],
        )
    return (
        (left, left_center, left_half),
        (right, right_center, right_half),
    )


def expand_macro_level(
    circuit: PhysicalCircuit,
    seed_positions: Mapping[int, Position],
    coarse_geometry: MacroGeometry,
    finer_level: CoarseningLevel,
    *,
    target_density: float = 0.80,
) -> MacroGeometry:
    """Subdivide every optimized parent region into its one or two finer children."""

    if not 0.0 < target_density <= 1.0:
        raise ValueError("target_density must be in (0, 1]")
    implementation_ids = {entity.id for entity in circuit.entities}
    if set(seed_positions) != implementation_ids:
        raise ValueError("seed_positions must exactly cover implementation entities")

    parents = child_parent_indices(coarse_geometry.level, finer_level)
    children_by_parent: dict[int, list[int]] = defaultdict(list)
    for child_index, parent_index in enumerate(parents):
        children_by_parent[parent_index].append(child_index)

    centers: list[Position | None] = [None] * len(finer_level.macros)
    half_extents: list[tuple[float, float] | None] = [None] * len(finer_level.macros)
    for parent_index in range(len(coarse_geometry.level.macros)):
        children = tuple(children_by_parent.get(parent_index, ()))
        if not children:
            raise ValueError(f"coarse macro {parent_index} has no finer children")
        for child_index, center, half in _subdivide_parent_region(
            circuit,
            coarse_geometry,
            finer_level,
            parent_index,
            children,
            target_density=target_density,
        ):
            centers[child_index] = center
            half_extents[child_index] = half

    if any(center is None for center in centers) or any(half is None for half in half_extents):
        raise AssertionError("hierarchical region subdivision left an unassigned child")
    result = MacroGeometry(
        finer_level,
        tuple(center for center in centers if center is not None),
        tuple(half for half in half_extents if half is not None),
        coarse_geometry.implementation_area,
    )
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
    """Walk every hierarchy level back to singleton macro regions with bounded refinement."""

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

        # Fine-level C4 refinement is optional and transactional. In particular, there is no
        # unconditional area-only zoom between subdivisions: the inherited region geometry remains
        # available as the best state when a proposed move loses more net quality than it gains.
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
