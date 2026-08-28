"""Authoritative rigid-component geometry for physical layout optimization.

Milestone D1 deliberately stops before rigid-body motion. A component describes its geometry in a
local coordinate system and a current pose, while lowering freezes its current members and removes
component-owned/keepout/adapter regions from the ordinary placement lattice. This makes geometry
authoritative for implementation combinators and relay routing without teaching the annealer unsafe
partial rigid-body semantics. Milestone D2 can replace the frozen lowering with rigid moves while
preserving this public contract.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.layout import Layout
from factorio_circuit.synthesis.layout_optimizer import (
    LayoutOptimizationProblem,
    LayoutOptimizationResult,
    LegalPlacementLattice,
    optimize_physical_layout,
    validate_physical_layout,
)
from factorio_circuit.synthesis.placement import PlacementOptions, Position

_EPSILON = 1e-9
HalfExtent = tuple[float, float]


@dataclass(frozen=True, slots=True)
class ComponentRegion:
    """Positive-area rectangle expressed in component-local coordinates."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def __post_init__(self) -> None:
        if self.max_x <= self.min_x or self.max_y <= self.min_y:
            raise ValueError("component region must have positive width and height")

    def transformed(self, origin: Position, quarter_turns: int) -> ComponentRegion:
        """Return the axis-aligned absolute rectangle after a quarter-turn rigid pose."""

        corners = (
            (self.min_x, self.min_y),
            (self.min_x, self.max_y),
            (self.max_x, self.min_y),
            (self.max_x, self.max_y),
        )
        transformed = [_absolute_position(origin, point, quarter_turns) for point in corners]
        xs = [point[0] for point in transformed]
        ys = [point[1] for point in transformed]
        return ComponentRegion(min(xs), min(ys), max(xs), max(ys))

    def contains_box(self, position: Position, half: HalfExtent) -> bool:
        """Return true when a complete physical box lies inside this region."""

        return (
            position[0] - half[0] >= self.min_x - _EPSILON
            and position[0] + half[0] <= self.max_x + _EPSILON
            and position[1] - half[1] >= self.min_y - _EPSILON
            and position[1] + half[1] <= self.max_y + _EPSILON
        )

    def overlaps_box(self, position: Position, half: HalfExtent) -> bool:
        """Return true for positive-area overlap; touching boundaries is legal."""

        return (
            position[0] + half[0] > self.min_x + _EPSILON
            and position[0] - half[0] < self.max_x - _EPSILON
            and position[1] + half[1] > self.min_y + _EPSILON
            and position[1] - half[1] < self.max_y - _EPSILON
        )

    def interior_overlaps(self, other: ComponentRegion) -> bool:
        return (
            min(self.max_x, other.max_x) - max(self.min_x, other.min_x) > _EPSILON
            and min(self.max_y, other.max_y) - max(self.min_y, other.min_y) > _EPSILON
        )

    def contains_boundary_point(self, point: Position) -> bool:
        x, y = point
        inside = (
            self.min_x - _EPSILON <= x <= self.max_x + _EPSILON
            and self.min_y - _EPSILON <= y <= self.max_y + _EPSILON
        )
        if not inside:
            return False
        return (
            abs(x - self.min_x) <= _EPSILON
            or abs(x - self.max_x) <= _EPSILON
            or abs(y - self.min_y) <= _EPSILON
            or abs(y - self.max_y) <= _EPSILON
        )


@dataclass(frozen=True, slots=True)
class RigidComponentMember:
    """One physical layout object at a fixed local offset from the component origin."""

    object_id: int
    offset: Position

    def __post_init__(self) -> None:
        if self.object_id <= 0:
            raise ValueError("rigid component member id must be positive")


@dataclass(frozen=True, slots=True)
class ComponentAccessPoint:
    """Named public routing/access point on a declared footprint boundary."""

    name: str
    offset: Position

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("component access point name must be non-empty")


@dataclass(frozen=True, slots=True)
class RigidComponentConstraint:
    """Rigid local geometry plus the currently selected legal component pose.

    ``footprints`` are component-owned regions. Current members must fit completely inside one of
    them, while every object outside this component must stay out. ``keepouts`` extend that external
    exclusion without constraining where the component's own members may lie.
    ``adapter_regions`` are reserved for a future interface adapter/routing stage and must currently
    be empty, including of the component's own members.

    ``allowed_origins`` and ``allowed_quarter_turns`` are represented in D1 so D2 can add rigid-body
    moves without changing the constraint format. D1 preserves the selected pose exactly.
    """

    name: str
    origin: Position
    members: tuple[RigidComponentMember, ...]
    footprints: tuple[ComponentRegion, ...]
    keepouts: tuple[ComponentRegion, ...] = ()
    adapter_regions: tuple[ComponentRegion, ...] = ()
    access_points: tuple[ComponentAccessPoint, ...] = ()
    quarter_turns: int = 0
    allowed_origins: tuple[Position, ...] | None = None
    allowed_quarter_turns: tuple[int, ...] = (0,)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("rigid component name must be non-empty")
        if not self.members:
            raise ValueError("rigid component must contain at least one physical member")
        if not self.footprints:
            raise ValueError("rigid component must declare at least one footprint region")
        if self.quarter_turns not in range(4):
            raise ValueError("component quarter_turns must be one of 0, 1, 2, 3")
        if not self.allowed_quarter_turns:
            raise ValueError("component allowed_quarter_turns must not be empty")
        if any(turn not in range(4) for turn in self.allowed_quarter_turns):
            raise ValueError("component allowed quarter turns must be one of 0, 1, 2, 3")
        if len(set(self.allowed_quarter_turns)) != len(self.allowed_quarter_turns):
            raise ValueError("component allowed_quarter_turns contains duplicates")
        if self.quarter_turns not in self.allowed_quarter_turns:
            raise ValueError("current component orientation is not allowed")
        if self.allowed_origins is not None:
            if not self.allowed_origins:
                raise ValueError("component allowed_origins must be None or non-empty")
            if len(set(self.allowed_origins)) != len(self.allowed_origins):
                raise ValueError("component allowed_origins contains duplicates")
            if self.origin not in self.allowed_origins:
                raise ValueError("current component origin is not allowed")

        member_ids = [member.object_id for member in self.members]
        if len(set(member_ids)) != len(member_ids):
            raise ValueError("rigid component contains duplicate member ids")
        access_names = [access.name for access in self.access_points]
        if len(set(access_names)) != len(access_names):
            raise ValueError("rigid component contains duplicate access point names")
        for left_index, left in enumerate(self.footprints):
            for right in self.footprints[left_index + 1 :]:
                if left.interior_overlaps(right):
                    raise ValueError(
                        "component footprint regions must not overlap in their interiors"
                    )
        for access in self.access_points:
            if not any(region.contains_boundary_point(access.offset) for region in self.footprints):
                raise ValueError(
                    f"component access point {access.name!r} is not on a footprint boundary"
                )

    @property
    def member_ids(self) -> frozenset[int]:
        return frozenset(member.object_id for member in self.members)

    def member_positions(self) -> dict[int, Position]:
        return {
            member.object_id: _absolute_position(self.origin, member.offset, self.quarter_turns)
            for member in self.members
        }

    def absolute_footprints(self) -> tuple[ComponentRegion, ...]:
        return tuple(
            region.transformed(self.origin, self.quarter_turns) for region in self.footprints
        )

    def absolute_keepouts(self) -> tuple[ComponentRegion, ...]:
        return tuple(
            region.transformed(self.origin, self.quarter_turns) for region in self.keepouts
        )

    def absolute_adapter_regions(self) -> tuple[ComponentRegion, ...]:
        return tuple(
            region.transformed(self.origin, self.quarter_turns) for region in self.adapter_regions
        )

    def access_positions(self) -> dict[str, Position]:
        return {
            access.name: _absolute_position(self.origin, access.offset, self.quarter_turns)
            for access in self.access_points
        }


@dataclass(frozen=True, slots=True)
class ComponentLayoutOptimizationProblem:
    """Physical layout optimization problem plus authoritative rigid component geometry."""

    layout_problem: LayoutOptimizationProblem
    components: tuple[RigidComponentConstraint, ...]

    def __post_init__(self) -> None:
        names = [component.name for component in self.components]
        if len(set(names)) != len(names):
            raise ValueError("component layout problem contains duplicate component names")


def validate_component_layout_problem(problem: ComponentLayoutOptimizationProblem) -> None:
    """Validate component geometry and the exact lowered physical artifact."""

    lower_component_layout_problem(problem, validate_base=True)


def lower_component_layout_problem(
    problem: ComponentLayoutOptimizationProblem,
    *,
    validate_base: bool = True,
) -> LayoutOptimizationProblem:
    """Lower D1 geometry to fixed members plus a component-aware legal lattice.

    Component membership is resolved *before* ordinary lattice validation. A rigid member may be at
    a legal component pose that is not itself a movable annealer site. Once promoted to
    ``fixed_positions``, the ordinary exact validator treats that coordinate as part of the physical
    boundary.
    """

    base = problem.layout_problem
    positions = base.layout.positions
    half_extents = _layout_half_extents(base.layout)
    known_ids = set(positions)
    member_owner: dict[int, str] = {}
    fixed_positions = dict(base.fixed_positions)
    exclusion_regions: list[ComponentRegion] = []
    owned_footprints: list[tuple[str, ComponentRegion]] = []

    for component in problem.components:
        for member_id in component.member_ids:
            previous = member_owner.setdefault(member_id, component.name)
            if previous != component.name:
                raise ValueError(
                    f"physical object {member_id} belongs to both component {previous!r} and "
                    f"{component.name!r}"
                )
        unknown = sorted(component.member_ids - known_ids)
        if unknown:
            raise ValueError(f"component {component.name!r} refers to unknown object ids {unknown}")

        expected_positions = component.member_positions()
        footprints = component.absolute_footprints()
        keepouts = component.absolute_keepouts()
        adapters = component.absolute_adapter_regions()
        member_ids = component.member_ids

        for footprint in footprints:
            for previous_name, previous_footprint in owned_footprints:
                if footprint.interior_overlaps(previous_footprint):
                    raise ValueError(
                        f"component {component.name!r} footprint overlaps component "
                        f"{previous_name!r} footprint"
                    )
            owned_footprints.append((component.name, footprint))

        for object_id, expected in expected_positions.items():
            actual = positions[object_id]
            if actual != expected:
                raise ValueError(
                    f"component {component.name!r} member {object_id} is not at its rigid pose: "
                    f"expected {expected!r}, got {actual!r}"
                )
            if not any(
                footprint.contains_box(actual, half_extents[object_id]) for footprint in footprints
            ):
                raise ValueError(
                    f"component {component.name!r} member {object_id} does not fit completely "
                    "inside any component footprint"
                )
            existing = fixed_positions.get(object_id)
            if existing is not None and existing != expected:
                raise ValueError(
                    f"component {component.name!r} member {object_id} conflicts with "
                    "a fixed position"
                )
            fixed_positions[object_id] = expected

        external_exclusions = (*footprints, *keepouts)
        for object_id, position in positions.items():
            half = half_extents[object_id]
            if object_id not in member_ids and any(
                region.overlaps_box(position, half) for region in external_exclusions
            ):
                raise ValueError(
                    f"physical object {object_id} overlaps component {component.name!r} "
                    "owned/keepout geometry"
                )
            if any(region.overlaps_box(position, half) for region in adapters):
                raise ValueError(
                    f"physical object {object_id} overlaps component {component.name!r} "
                    "reserved adapter region"
                )

        exclusion_regions.extend((*footprints, *keepouts, *adapters))

    lowered = replace(
        base,
        lattice=_filter_lattice(base.lattice, tuple(exclusion_regions)),
        fixed_positions=fixed_positions,
    )
    if validate_base:
        validate_physical_layout(lowered)
    return lowered


def optimize_component_layout(
    problem: ComponentLayoutOptimizationProblem,
    *,
    options: PlacementOptions,
) -> LayoutOptimizationResult:
    """Optimize while preserving D1 component geometry transactionally."""

    lowered = lower_component_layout_problem(problem)
    result = optimize_physical_layout(lowered, options=options)
    output_problem = replace(
        problem,
        layout_problem=replace(problem.layout_problem, layout=result.layout),
    )
    try:
        validate_component_layout_problem(output_problem)
    except ValueError as exc:
        return LayoutOptimizationResult(
            layout=problem.layout_problem.layout,
            before=result.before,
            after=result.before,
            proposal_budget=result.proposal_budget,
            diagnostics=(*result.diagnostics, f"component geometry candidate rejected: {exc}"),
        )
    return result


def _filter_lattice(
    lattice: LegalPlacementLattice,
    regions: tuple[ComponentRegion, ...],
) -> LegalPlacementLattice:
    if not regions:
        return lattice

    def clear(position: Position, half: HalfExtent) -> bool:
        return not any(region.overlaps_box(position, half) for region in regions)

    return LegalPlacementLattice(
        unit_sites=tuple(site for site in lattice.unit_sites if clear(site, (0.5, 0.5))),
        wide_sites=tuple(site for site in lattice.wide_sites if clear(site, (1.0, 0.5))),
        forbidden_areas=lattice.forbidden_areas,
    )


def _layout_half_extents(layout: Layout) -> dict[int, HalfExtent]:
    relay_ids = {relay.entity_id for relay in layout.relays}
    entities = {entity.id: entity for entity in layout.circuit.entities}
    return {
        object_id: (
            (0.5, 0.5)
            if object_id in relay_ids
            else base_placement._entity_half_extent(entities[object_id])
        )
        for object_id in layout.positions
    }


def _absolute_position(origin: Position, local: Position, quarter_turns: int) -> Position:
    x, y = _rotate(local, quarter_turns)
    return (origin[0] + x, origin[1] + y)


def _rotate(position: Position, quarter_turns: int) -> Position:
    x, y = position
    match quarter_turns % 4:
        case 0:
            return (x, y)
        case 1:
            return (-y, x)
        case 2:
            return (-x, -y)
        case 3:
            return (y, -x)
    raise AssertionError("quarter-turn normalization failed")


__all__ = [
    "ComponentAccessPoint",
    "ComponentLayoutOptimizationProblem",
    "ComponentRegion",
    "RigidComponentConstraint",
    "RigidComponentMember",
    "lower_component_layout_problem",
    "optimize_component_layout",
    "validate_component_layout_problem",
]
