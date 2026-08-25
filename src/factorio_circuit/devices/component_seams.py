"""Constrained physical component boundaries and seam composition.

The low-level :mod:`factorio_circuit.devices.anchors` API proves electrical compatibility between
named terminals. This module adds the geometric contract needed for reusable physical modules:
component confinement, boundary side/slot placement, ordered named seams, and composition whose
translation is derived from the seam rather than supplied as an arbitrary coordinate offset.

Coordinates in :class:`ComponentFootprint` bound entity *centres*. Prototype-specific collision
boxes remain the responsibility of the component generator; this layer deliberately avoids a hidden
prototype-size database while still preventing wandering implementation entities and floating docks.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from factorio_circuit.devices.anchors import (
    AnchorBinding,
    AnchoredBlueprint,
    AnchorSpec,
    BoundAnchor,
    compose_anchored_blueprints,
)

_EPSILON = 1e-9


class ComponentSide(StrEnum):
    """One outward-facing side of a rectangular component footprint."""

    NORTH = "north"
    EAST = "east"
    SOUTH = "south"
    WEST = "west"

    @property
    def opposite(self) -> ComponentSide:
        return {
            ComponentSide.NORTH: ComponentSide.SOUTH,
            ComponentSide.EAST: ComponentSide.WEST,
            ComponentSide.SOUTH: ComponentSide.NORTH,
            ComponentSide.WEST: ComponentSide.EAST,
        }[self]


@dataclass(frozen=True, slots=True)
class ComponentFootprint:
    """Rectangular allowed region for entity centres and boundary slots."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float
    slot_pitch: float = 1.0

    def __post_init__(self) -> None:
        if self.max_x <= self.min_x or self.max_y <= self.min_y:
            raise ValueError("component footprint must have positive width and height")
        if self.slot_pitch <= 0:
            raise ValueError("component slot pitch must be positive")

    def boundary_position(self, side: ComponentSide, slot: int) -> tuple[float, float]:
        """Return the absolute coordinate of one integer slot on ``side``."""

        if slot < 0:
            raise ValueError("component boundary slot must be non-negative")
        offset = slot * self.slot_pitch
        if side in {ComponentSide.WEST, ComponentSide.EAST}:
            y = self.min_y + offset
            if y > self.max_y + _EPSILON:
                raise ValueError(f"slot {slot} lies beyond {side.value} side")
            x = self.min_x if side is ComponentSide.WEST else self.max_x
            return (x, y)
        x = self.min_x + offset
        if x > self.max_x + _EPSILON:
            raise ValueError(f"slot {slot} lies beyond {side.value} side")
        y = self.min_y if side is ComponentSide.NORTH else self.max_y
        return (x, y)

    def contains(self, position: tuple[float, float]) -> bool:
        x, y = position
        return (
            self.min_x - _EPSILON <= x <= self.max_x + _EPSILON
            and self.min_y - _EPSILON <= y <= self.max_y + _EPSILON
        )

    def translated(self, offset: tuple[float, float]) -> ComponentFootprint:
        dx, dy = offset
        return ComponentFootprint(
            self.min_x + dx,
            self.min_y + dy,
            self.max_x + dx,
            self.max_y + dy,
            self.slot_pitch,
        )

    def interior_overlaps(self, other: ComponentFootprint) -> bool:
        """Return true for positive-area overlap; touching boundaries are legal seams."""

        return (
            min(self.max_x, other.max_x) - max(self.min_x, other.min_x) > _EPSILON
            and min(self.max_y, other.max_y) - max(self.min_y, other.min_y) > _EPSILON
        )


@dataclass(frozen=True, slots=True)
class BoundarySlot:
    """Bind one named anchor to a side/slot of one footprint in an assembly."""

    anchor: str
    side: ComponentSide
    slot: int
    footprint_index: int = 0

    def __post_init__(self) -> None:
        if not self.anchor:
            raise ValueError("boundary slot anchor name must be non-empty")
        if self.slot < 0:
            raise ValueError("boundary slot must be non-negative")
        if self.footprint_index < 0:
            raise ValueError("boundary slot footprint index must be non-negative")


@dataclass(frozen=True, slots=True)
class ComponentSeam:
    """Ordered group of boundary anchors that must compose as one physical seam."""

    name: str
    side: ComponentSide
    anchors: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("component seam name must be non-empty")
        if not self.anchors:
            raise ValueError("component seam must contain at least one anchor")
        if len(set(self.anchors)) != len(self.anchors):
            raise ValueError(f"component seam {self.name!r} contains duplicate anchors")


@dataclass(frozen=True, slots=True)
class ConstrainedComponent:
    """An anchored blueprint whose implementation and interface geometry are confined."""

    anchored: AnchoredBlueprint
    footprints: tuple[ComponentFootprint, ...]
    slots: tuple[BoundarySlot, ...]
    seams: tuple[ComponentSeam, ...]

    def __post_init__(self) -> None:
        if not self.footprints:
            raise ValueError("constrained component requires at least one footprint")
        for left_index, left in enumerate(self.footprints):
            for right in self.footprints[left_index + 1 :]:
                if left.interior_overlaps(right):
                    raise ValueError("component footprints must not overlap in their interiors")

        anchors = {anchor.name: anchor for anchor in self.anchored.anchors}
        slot_names = [slot.anchor for slot in self.slots]
        if len(set(slot_names)) != len(slot_names):
            raise ValueError("one anchor cannot occupy multiple boundary slots")
        if set(slot_names) != set(anchors):
            missing = sorted(set(anchors) - set(slot_names))
            extra = sorted(set(slot_names) - set(anchors))
            raise ValueError(
                f"constrained component boundary slots must cover exactly its anchors; "
                f"missing={missing!r}, extra={extra!r}"
            )

        slots_by_name = {slot.anchor: slot for slot in self.slots}
        dock_positions: list[tuple[str, tuple[float, float]]] = []
        for slot in self.slots:
            try:
                footprint = self.footprints[slot.footprint_index]
            except IndexError as exc:
                raise ValueError(
                    f"boundary slot {slot.anchor!r} references missing footprint "
                    f"{slot.footprint_index}"
                ) from exc
            expected = footprint.boundary_position(slot.side, slot.slot)
            actual = anchors[slot.anchor].position
            if not _same_position(expected, actual):
                raise ValueError(
                    f"anchor {slot.anchor!r} is not on its declared {slot.side.value} slot "
                    f"{slot.slot}: expected {expected!r}, got {actual!r}"
                )
            for previous_name, previous_position in dock_positions:
                if _same_position(expected, previous_position):
                    raise ValueError(
                        f"boundary anchors {previous_name!r} and {slot.anchor!r} occupy the same "
                        f"dock coordinate {expected!r}"
                    )
            dock_positions.append((slot.anchor, expected))

        seam_names = [seam.name for seam in self.seams]
        if len(set(seam_names)) != len(seam_names):
            raise ValueError("constrained component contains duplicate seam names")
        seam_anchor_names = [anchor for seam in self.seams for anchor in seam.anchors]
        if len(set(seam_anchor_names)) != len(seam_anchor_names):
            raise ValueError("one boundary anchor cannot belong to multiple seams")
        if set(seam_anchor_names) != set(anchors):
            raise ValueError("every constrained boundary anchor must belong to exactly one seam")
        for seam in self.seams:
            seam_slots = [slots_by_name[name] for name in seam.anchors]
            if any(slot.side is not seam.side for slot in seam_slots):
                raise ValueError(f"seam {seam.name!r} contains an anchor on the wrong side")
            footprint_indices = {slot.footprint_index for slot in seam_slots}
            if len(footprint_indices) != 1:
                raise ValueError(f"seam {seam.name!r} cannot span multiple component footprints")
            indices = [slot.slot for slot in seam_slots]
            if indices != sorted(indices):
                raise ValueError(f"seam {seam.name!r} anchors must be ordered by boundary slot")

        for entity in _entities(self.anchored):
            position = _position(entity)
            if not any(footprint.contains(position) for footprint in self.footprints):
                raise ValueError(
                    f"entity {entity.get('entity_number')!r} at {position!r} lies outside every "
                    "component footprint"
                )

    @classmethod
    def bounded(
        cls,
        anchored: AnchoredBlueprint,
        footprint: ComponentFootprint,
        *,
        slots: Iterable[BoundarySlot],
        seams: Iterable[ComponentSeam],
    ) -> ConstrainedComponent:
        """Construct one single-footprint constrained component."""

        normalized_slots = tuple(slots)
        if any(slot.footprint_index != 0 for slot in normalized_slots):
            raise ValueError("single-footprint component slots must use footprint_index=0")
        return cls(anchored, (footprint,), normalized_slots, tuple(seams))

    def seam(self, name: str) -> ComponentSeam:
        for seam in self.seams:
            if seam.name == name:
                return seam
        raise KeyError(name)

    def slot(self, anchor: str) -> BoundarySlot:
        for slot in self.slots:
            if slot.anchor == anchor:
                return slot
        raise KeyError(anchor)


def boundary_anchor(
    spec: AnchorSpec,
    entity_number: int,
    connector_id: int,
    footprint: ComponentFootprint,
    *,
    side: ComponentSide,
    slot: int,
) -> BoundAnchor:
    """Create a low-level bound anchor with its coordinate derived from side/slot geometry."""

    return BoundAnchor(
        spec,
        entity_number,
        connector_id,
        footprint.boundary_position(side, slot),
    )


def compose_component_seams(
    left: ConstrainedComponent,
    right: ConstrainedComponent,
    *,
    left_seam: str,
    right_seam: str,
    label: str | None = None,
) -> ConstrainedComponent:
    """Compose opposite seams and derive the unique translation automatically."""

    left_spec = left.seam(left_seam)
    right_spec = right.seam(right_seam)
    if right_spec.side is not left_spec.side.opposite:
        raise ValueError(
            f"seams must face opposite directions, got {left_spec.side.value}/"
            f"{right_spec.side.value}"
        )
    if len(left_spec.anchors) != len(right_spec.anchors):
        raise ValueError("seams must contain the same number of lanes")

    bindings = tuple(
        AnchorBinding(left_name, right_name)
        for left_name, right_name in zip(left_spec.anchors, right_spec.anchors, strict=True)
    )
    offsets = [
        _offset(
            left.anchored.anchor(binding.left).position,
            right.anchored.anchor(binding.right).position,
        )
        for binding in bindings
    ]
    offset = offsets[0]
    if any(not _same_position(candidate, offset) for candidate in offsets[1:]):
        raise ValueError("seam lanes do not imply one rigid component translation")

    translated_right_footprints = tuple(
        footprint.translated(offset) for footprint in right.footprints
    )
    if any(
        left_footprint.interior_overlaps(right_footprint)
        for left_footprint in left.footprints
        for right_footprint in translated_right_footprints
    ):
        raise ValueError("seam composition would overlap component interiors")

    composed = compose_anchored_blueprints(
        left.anchored,
        right.anchored,
        bindings=bindings,
        right_offset=offset,
        label=label,
    )
    anchored = AnchoredBlueprint(
        composed.blueprint,
        composed.anchors,
        label or f"{left.anchored.label}+{right.anchored.label}",
    )

    consumed_left = set(left_spec.anchors)
    consumed_right = set(right_spec.anchors)
    surviving_slots: list[BoundarySlot] = [
        slot for slot in left.slots if slot.anchor not in consumed_left
    ]
    right_footprint_offset = len(left.footprints)
    surviving_slots.extend(
        BoundarySlot(
            slot.anchor,
            slot.side,
            slot.slot,
            slot.footprint_index + right_footprint_offset,
        )
        for slot in right.slots
        if slot.anchor not in consumed_right
    )
    surviving_seams = tuple(seam for seam in left.seams if seam.name != left_seam) + tuple(
        seam for seam in right.seams if seam.name != right_seam
    )

    return ConstrainedComponent(
        anchored,
        left.footprints + translated_right_footprints,
        tuple(surviving_slots),
        surviving_seams,
    )


def _entities(component: AnchoredBlueprint) -> list[dict[str, object]]:
    raw = component.blueprint.get("entities", [])
    if not isinstance(raw, list) or not all(isinstance(entity, dict) for entity in raw):
        raise ValueError("component blueprint entities must be dictionaries")
    return raw  # type: ignore[return-value]


def _position(entity: dict[str, object]) -> tuple[float, float]:
    raw = entity.get("position")
    if not isinstance(raw, dict):
        raise ValueError(f"entity {entity.get('entity_number')!r} has no position")
    return float(raw["x"]), float(raw["y"])


def _offset(left: tuple[float, float], right: tuple[float, float]) -> tuple[float, float]:
    return left[0] - right[0], left[1] - right[1]


def _same_position(left: tuple[float, float], right: tuple[float, float]) -> bool:
    return abs(left[0] - right[0]) <= _EPSILON and abs(left[1] - right[1]) <= _EPSILON
