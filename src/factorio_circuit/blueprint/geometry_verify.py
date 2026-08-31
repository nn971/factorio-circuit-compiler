"""Independent ABI and rigid-geometry verification for serialized Factorio blueprints.

I3 consumes only the emitted blueprint artifact plus explicit post-serialization geometry
expectations. It deliberately does not import synthesis ``Layout`` state, rigid-component
constraints, anchor bindings, or seam composition objects.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import cast

from factorio_circuit.blueprint.verify import (
    BlueprintPrototypeSpec,
    BlueprintVerificationError,
    decode_blueprint_string,
    verify_blueprint_structure,
)

_EPSILON = 1e-9
Position = tuple[float, float]
HalfExtent = tuple[float, float]


@dataclass(frozen=True, slots=True)
class BlueprintRegion:
    """Positive-area axis-aligned rectangle in an expectation's local coordinates."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def __post_init__(self) -> None:
        values = (self.min_x, self.min_y, self.max_x, self.max_y)
        if not all(isfinite(value) for value in values):
            raise ValueError("blueprint geometry region coordinates must be finite")
        if self.max_x <= self.min_x or self.max_y <= self.min_y:
            raise ValueError("blueprint geometry region must have positive width and height")

    def transformed(self, origin: Position, quarter_turns: int) -> BlueprintRegion:
        """Return the absolute axis-aligned region after a quarter-turn component pose."""

        corners = (
            (self.min_x, self.min_y),
            (self.min_x, self.max_y),
            (self.max_x, self.min_y),
            (self.max_x, self.max_y),
        )
        points = [_absolute_position(origin, corner, quarter_turns) for corner in corners]
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        return BlueprintRegion(min(xs), min(ys), max(xs), max(ys))

    def contains_box(self, position: Position, half_extent: HalfExtent) -> bool:
        return (
            position[0] - half_extent[0] >= self.min_x - _EPSILON
            and position[0] + half_extent[0] <= self.max_x + _EPSILON
            and position[1] - half_extent[1] >= self.min_y - _EPSILON
            and position[1] + half_extent[1] <= self.max_y + _EPSILON
        )

    def overlaps_box(self, position: Position, half_extent: HalfExtent) -> bool:
        """Return true for positive-area overlap; boundary touching is accepted."""

        return (
            position[0] + half_extent[0] > self.min_x + _EPSILON
            and position[0] - half_extent[0] < self.max_x - _EPSILON
            and position[1] + half_extent[1] > self.min_y + _EPSILON
            and position[1] - half_extent[1] < self.max_y - _EPSILON
        )

    def contains_boundary_point(self, position: Position) -> bool:
        x, y = position
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
class BlueprintAnchorExpectation:
    """One exact serialized ABI endpoint at a prescribed entity-centre position."""

    name: str
    entity_id: int
    connector_id: int
    position: Position
    prototype: str | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("blueprint anchor name must be non-empty")
        _validate_positive_int(self.entity_id, "blueprint anchor entity id")
        _validate_positive_int(self.connector_id, "blueprint anchor connector id")
        _validate_position(self.position, "blueprint anchor position")
        if self.prototype is not None and not self.prototype:
            raise ValueError("blueprint anchor prototype must be non-empty when supplied")


@dataclass(frozen=True, slots=True)
class BlueprintSeamExpectation:
    """Named boundary seam whose anchors must remain at exact serialized positions."""

    name: str
    boundary: BlueprintRegion
    anchors: tuple[BlueprintAnchorExpectation, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("blueprint seam name must be non-empty")
        if not self.anchors:
            raise ValueError("blueprint seam must contain at least one anchor")
        names = [anchor.name for anchor in self.anchors]
        if len(set(names)) != len(names):
            raise ValueError(f"blueprint seam {self.name!r} contains duplicate anchor names")
        endpoints = [(anchor.entity_id, anchor.connector_id) for anchor in self.anchors]
        if len(set(endpoints)) != len(endpoints):
            raise ValueError(f"blueprint seam {self.name!r} contains duplicate endpoints")
        for anchor in self.anchors:
            if not self.boundary.contains_boundary_point(anchor.position):
                raise ValueError(
                    f"blueprint seam {self.name!r} anchor {anchor.name!r} is not on its boundary"
                )


@dataclass(frozen=True, slots=True)
class BlueprintRigidMemberExpectation:
    """One serialized entity at a fixed local offset from a rigid component origin."""

    entity_id: int
    offset: Position
    prototype: str | None = None

    def __post_init__(self) -> None:
        _validate_positive_int(self.entity_id, "rigid member entity id")
        _validate_position(self.offset, "rigid member offset")
        if self.prototype is not None and not self.prototype:
            raise ValueError("rigid member prototype must be non-empty when supplied")


@dataclass(frozen=True, slots=True)
class BlueprintRigidComponentExpectation:
    """Explicit post-serialization contract for one rigid component pose and its regions.

    ``footprints`` are owned regions: members must fit inside one footprint and outsiders must not
    overlap them. ``keepouts`` exclude outsiders but may contain component members.
    ``adapter_regions`` must remain empty even of the component's own members.
    """

    name: str
    origin: Position
    members: tuple[BlueprintRigidMemberExpectation, ...]
    footprints: tuple[BlueprintRegion, ...]
    keepouts: tuple[BlueprintRegion, ...] = ()
    adapter_regions: tuple[BlueprintRegion, ...] = ()
    quarter_turns: int = 0

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("rigid component expectation name must be non-empty")
        _validate_position(self.origin, "rigid component origin")
        if self.quarter_turns not in range(4):
            raise ValueError("rigid component quarter_turns must be one of 0, 1, 2, 3")
        if not self.members:
            raise ValueError("rigid component expectation must contain at least one member")
        if not self.footprints:
            raise ValueError("rigid component expectation must contain at least one footprint")
        member_ids = [member.entity_id for member in self.members]
        if len(set(member_ids)) != len(member_ids):
            raise ValueError(f"rigid component {self.name!r} contains duplicate member ids")


@dataclass(frozen=True, slots=True)
class BlueprintGeometryReport:
    """Success summary for independent serialized geometry verification."""

    verified_anchors: tuple[str, ...]
    verified_seams: tuple[str, ...]
    verified_components: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _GeometryEntity:
    entity_id: int
    prototype: str
    position: Position
    spec: BlueprintPrototypeSpec
    half_extent: HalfExtent


def verify_blueprint_geometry(
    artifact: Mapping[str, object] | str,
    *,
    prototype_specs: Mapping[str, BlueprintPrototypeSpec],
    anchors: Sequence[BlueprintAnchorExpectation] = (),
    seams: Sequence[BlueprintSeamExpectation] = (),
    components: Sequence[BlueprintRigidComponentExpectation] = (),
) -> BlueprintGeometryReport:
    """Verify exact anchors, seam boundaries, and rigid regions from serialized geometry only."""

    root = decode_blueprint_string(artifact) if isinstance(artifact, str) else artifact
    verify_blueprint_structure(root, prototype_specs=prototype_specs)
    entities = _parse_entities(_blueprint_body(root), prototype_specs)

    _verify_named_anchors(anchors, entities)
    _verify_seams(seams, entities)
    _verify_components(components, entities)

    return BlueprintGeometryReport(
        verified_anchors=tuple(anchor.name for anchor in anchors),
        verified_seams=tuple(seam.name for seam in seams),
        verified_components=tuple(component.name for component in components),
    )


def _verify_named_anchors(
    anchors: Sequence[BlueprintAnchorExpectation],
    entities: Mapping[int, _GeometryEntity],
) -> None:
    names: set[str] = set()
    endpoints: dict[tuple[int, int], str] = {}
    for anchor in anchors:
        if anchor.name in names:
            raise ValueError(f"duplicate blueprint anchor name {anchor.name!r}")
        names.add(anchor.name)
        endpoint = (anchor.entity_id, anchor.connector_id)
        previous = endpoints.setdefault(endpoint, anchor.name)
        if previous != anchor.name:
            raise ValueError(
                f"blueprint endpoint {endpoint} is declared by both {previous!r} and "
                f"{anchor.name!r}"
            )
        _verify_anchor(anchor, entities)


def _verify_seams(
    seams: Sequence[BlueprintSeamExpectation],
    entities: Mapping[int, _GeometryEntity],
) -> None:
    names: set[str] = set()
    for seam in seams:
        if seam.name in names:
            raise ValueError(f"duplicate blueprint seam name {seam.name!r}")
        names.add(seam.name)
        for anchor in seam.anchors:
            _verify_anchor(anchor, entities)
            if not seam.boundary.contains_boundary_point(anchor.position):
                raise BlueprintVerificationError(
                    f"blueprint seam {seam.name!r} anchor {anchor.name!r} is off its boundary"
                )


def _verify_anchor(
    anchor: BlueprintAnchorExpectation,
    entities: Mapping[int, _GeometryEntity],
) -> None:
    entity = _expected_entity(anchor.entity_id, entities, f"anchor {anchor.name!r}")
    if anchor.prototype is not None and entity.prototype != anchor.prototype:
        raise BlueprintVerificationError(
            f"blueprint anchor {anchor.name!r} expected prototype {anchor.prototype!r}, "
            f"got {entity.prototype!r}"
        )
    if anchor.connector_id not in entity.spec.connector_ids:
        raise BlueprintVerificationError(
            f"blueprint anchor {anchor.name!r} connector {anchor.connector_id} is not exposed by "
            f"prototype {entity.prototype!r}"
        )
    if not _same_position(entity.position, anchor.position):
        raise BlueprintVerificationError(
            f"blueprint anchor {anchor.name!r} moved: expected {anchor.position!r}, "
            f"got {entity.position!r}"
        )


def _verify_components(
    components: Sequence[BlueprintRigidComponentExpectation],
    entities: Mapping[int, _GeometryEntity],
) -> None:
    names: set[str] = set()
    member_owner: dict[int, str] = {}
    for component in components:
        if component.name in names:
            raise ValueError(f"duplicate rigid component expectation name {component.name!r}")
        names.add(component.name)
        for member in component.members:
            previous = member_owner.setdefault(member.entity_id, component.name)
            if previous != component.name:
                raise ValueError(
                    f"serialized entity {member.entity_id} belongs to both rigid components "
                    f"{previous!r} and {component.name!r}"
                )

    for component in components:
        _verify_component(component, entities)


def _verify_component(
    component: BlueprintRigidComponentExpectation,
    entities: Mapping[int, _GeometryEntity],
) -> None:
    member_ids = {member.entity_id for member in component.members}
    footprints = tuple(
        region.transformed(component.origin, component.quarter_turns)
        for region in component.footprints
    )
    keepouts = tuple(
        region.transformed(component.origin, component.quarter_turns)
        for region in component.keepouts
    )
    adapter_regions = tuple(
        region.transformed(component.origin, component.quarter_turns)
        for region in component.adapter_regions
    )

    for member in component.members:
        entity = _expected_entity(
            member.entity_id,
            entities,
            f"rigid component {component.name!r} member",
        )
        if member.prototype is not None and entity.prototype != member.prototype:
            raise BlueprintVerificationError(
                f"rigid component {component.name!r} member {member.entity_id} expected "
                f"prototype {member.prototype!r}, got {entity.prototype!r}"
            )
        expected = _absolute_position(component.origin, member.offset, component.quarter_turns)
        if not _same_position(entity.position, expected):
            raise BlueprintVerificationError(
                f"rigid component {component.name!r} member {member.entity_id} moved: "
                f"expected {expected!r}, got {entity.position!r}"
            )
        if not any(
            footprint.contains_box(entity.position, entity.half_extent) for footprint in footprints
        ):
            raise BlueprintVerificationError(
                f"rigid component {component.name!r} member {member.entity_id} does not fit "
                "inside any declared owned footprint"
            )

    for entity_id, entity in entities.items():
        if entity_id not in member_ids and any(
            region.overlaps_box(entity.position, entity.half_extent)
            for region in (*footprints, *keepouts)
        ):
            raise BlueprintVerificationError(
                f"serialized entity {entity_id} overlaps rigid component {component.name!r} "
                "owned/keepout geometry"
            )
        if any(
            region.overlaps_box(entity.position, entity.half_extent) for region in adapter_regions
        ):
            raise BlueprintVerificationError(
                f"serialized entity {entity_id} overlaps rigid component {component.name!r} "
                "reserved adapter region"
            )


def _blueprint_body(root: Mapping[str, object]) -> Mapping[str, object]:
    nested = root.get("blueprint")
    if nested is not None:
        return _require_mapping(nested, "blueprint wrapper member")
    if "entities" in root or root.get("item") == "blueprint":
        return root
    raise BlueprintVerificationError("artifact does not contain an ordinary blueprint")


def _parse_entities(
    blueprint: Mapping[str, object],
    prototype_specs: Mapping[str, BlueprintPrototypeSpec],
) -> dict[int, _GeometryEntity]:
    raw_entities = _require_sequence(blueprint.get("entities", ()), "blueprint entities")
    result: dict[int, _GeometryEntity] = {}
    for index, raw_entity in enumerate(raw_entities):
        entity = _require_mapping(raw_entity, f"blueprint entity entry {index}")
        entity_id = cast(int, entity["entity_number"])
        prototype = cast(str, entity["name"])
        raw_position = _require_mapping(
            entity["position"],
            f"blueprint entity {entity_id} position",
        )
        position = (
            _finite_number(raw_position["x"], f"blueprint entity {entity_id} x position"),
            _finite_number(raw_position["y"], f"blueprint entity {entity_id} y position"),
        )
        direction = entity.get("direction", 0)
        if type(direction) is not int:
            raise BlueprintVerificationError(
                f"blueprint entity {entity_id} direction must be an integer"
            )
        spec = prototype_specs[prototype]
        try:
            half_extent = spec.half_extent_for_direction(direction)
        except ValueError as exc:
            raise BlueprintVerificationError(
                f"blueprint entity {entity_id} prototype {prototype!r} has unsupported "
                f"direction {direction} for verifier geometry"
            ) from exc
        result[entity_id] = _GeometryEntity(
            entity_id,
            prototype,
            position,
            spec,
            half_extent,
        )
    return result


def _expected_entity(
    entity_id: int,
    entities: Mapping[int, _GeometryEntity],
    context: str,
) -> _GeometryEntity:
    try:
        return entities[entity_id]
    except KeyError as exc:
        raise BlueprintVerificationError(
            f"{context} refers to absent serialized entity {entity_id}"
        ) from exc


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
        case _:
            return (y, -x)


def _same_position(left: Position, right: Position) -> bool:
    return abs(left[0] - right[0]) <= _EPSILON and abs(left[1] - right[1]) <= _EPSILON


def _validate_positive_int(value: object, context: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{context} must be a positive integer")


def _validate_position(position: Position, context: str) -> None:
    if len(position) != 2 or not all(isfinite(value) for value in position):
        raise ValueError(f"{context} must contain two finite coordinates")


def _finite_number(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BlueprintVerificationError(f"{context} must be numeric")
    result = float(value)
    if not isfinite(result):
        raise BlueprintVerificationError(f"{context} must be finite")
    return result


def _require_mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise BlueprintVerificationError(f"{context} must be a string-keyed mapping")
    return cast(Mapping[str, object], value)


def _require_sequence(value: object, context: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise BlueprintVerificationError(f"{context} must be a sequence")
    return cast(Sequence[object], value)


__all__ = [
    "BlueprintAnchorExpectation",
    "BlueprintGeometryReport",
    "BlueprintRegion",
    "BlueprintRigidComponentExpectation",
    "BlueprintRigidMemberExpectation",
    "BlueprintSeamExpectation",
    "verify_blueprint_geometry",
]
