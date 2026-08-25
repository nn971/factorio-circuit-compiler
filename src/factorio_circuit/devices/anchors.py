"""Exact-overlap anchoring for composing independently generated device blueprints.

An anchor is a typed constant-combinator terminal owned by one component. Components are composed by
translating them until compatible anchors occupy exactly the same position and then merging the anchor
entities. The composer never invents a circuit wire between component internals.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass

from factorio_circuit.devices._blueprint import Blueprint
from factorio_circuit.devices.protocol import DevicePortDirection
from factorio_circuit.ir.physical import SignalId, WireColor
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality


@dataclass(frozen=True, slots=True)
class AnchorSpec:
    """Logical/electrical contract of one composable blueprint terminal."""

    name: str
    direction: DevicePortDirection
    payload_shape: PayloadShape
    modality: TemporalModality
    wire: WireColor
    signal: SignalId | None = None
    required: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("anchor name must be non-empty")
        if self.payload_shape is PayloadShape.SCALAR and self.signal is None:
            raise ValueError(f"scalar anchor {self.name!r} requires a fixed signal")
        if self.payload_shape is PayloadShape.VECTOR and self.signal is not None:
            raise ValueError(f"vector anchor {self.name!r} cannot reserve one fixed signal")


@dataclass(frozen=True, slots=True)
class BoundAnchor:
    """One typed anchor bound to a concrete constant-combinator connector."""

    spec: AnchorSpec
    entity_number: int
    connector_id: int
    position: tuple[float, float]

    def __post_init__(self) -> None:
        if self.entity_number < 1:
            raise ValueError("anchor entity_number must be positive")
        if self.connector_id < 1:
            raise ValueError("anchor connector_id must be positive")

    @property
    def name(self) -> str:
        return self.spec.name


@dataclass(frozen=True, slots=True)
class AnchoredBlueprint:
    """A blueprint plus stable named anchors intended for exact-overlap composition."""

    blueprint: Blueprint
    anchors: tuple[BoundAnchor, ...]
    label: str = "component"

    def __post_init__(self) -> None:
        names = [anchor.name for anchor in self.anchors]
        if len(set(names)) != len(names):
            raise ValueError(f"anchored blueprint {self.label!r} has duplicate anchor names")
        _validate_anchor_entities(self)
        for anchor in self.anchors:
            _validate_anchor_local_connectivity(self, anchor)

    def anchor(self, name: str) -> BoundAnchor:
        for anchor in self.anchors:
            if anchor.name == name:
                return anchor
        raise KeyError(name)


@dataclass(frozen=True, slots=True)
class AnchorBinding:
    """Connect one producer anchor to one consumer anchor by exact entity overlap."""

    left: str
    right: str


@dataclass(frozen=True, slots=True)
class ComposedAnchoredBlueprint:
    """Result of exact-overlap composition and the surviving unbound anchors."""

    blueprint: Blueprint
    anchors: tuple[BoundAnchor, ...]

    def anchor(self, name: str) -> BoundAnchor:
        for anchor in self.anchors:
            if anchor.name == name:
                return anchor
        raise KeyError(name)


def compose_anchored_blueprints(
    left: AnchoredBlueprint,
    right: AnchoredBlueprint,
    *,
    bindings: Sequence[AnchorBinding | tuple[str, str]],
    right_offset: tuple[float, float] = (0.0, 0.0),
    label: str | None = None,
) -> ComposedAnchoredBlueprint:
    """Compose two blueprints by merging matching anchor entities, never by adding a cross-wire.

    The caller chooses a translation for ``right``. Every requested pair must then occupy exactly
    the same physical position and have compatible direction/shape/modality/signal/wire contracts.
    Both endpoints must already be connected to their own component internals (or, for an OUTPUT
    anchor, be a non-empty constant combinator source). This catches electrically dead terminals
    before composition.
    """

    normalized = tuple(
        binding if isinstance(binding, AnchorBinding) else AnchorBinding(*binding)
        for binding in bindings
    )
    if not normalized:
        raise ValueError("anchored composition requires at least one binding")
    if len({binding.left for binding in normalized}) != len(normalized):
        raise ValueError("one left anchor cannot be bound more than once")
    if len({binding.right for binding in normalized}) != len(normalized):
        raise ValueError("one right anchor cannot be bound more than once")

    for binding in normalized:
        left_anchor = left.anchor(binding.left)
        right_anchor = right.anchor(binding.right)
        _validate_compatible(left_anchor.spec, right_anchor.spec)
        translated = (
            right_anchor.position[0] + right_offset[0],
            right_anchor.position[1] + right_offset[1],
        )
        if translated != left_anchor.position:
            raise ValueError(
                f"anchors {binding.left!r}/{binding.right!r} do not overlap: "
                f"{left_anchor.position!r} != {translated!r}"
            )

    left_bp = deepcopy(left.blueprint)
    right_bp = deepcopy(right.blueprint)
    left_entities = _entities(left_bp)
    right_entities = _entities(right_bp)
    left_wires = _wires(left_bp)
    right_wires = _wires(right_bp)

    next_id = max((int(entity["entity_number"]) for entity in left_entities), default=0) + 1
    left_anchor_ids = {anchor.name: anchor.entity_number for anchor in left.anchors}
    bound_right_to_left = {
        right.anchor(binding.right).entity_number: left_anchor_ids[binding.left]
        for binding in normalized
    }

    right_id_map: dict[int, int] = {}
    for entity in right_entities:
        old_id = int(entity["entity_number"])
        if old_id in bound_right_to_left:
            right_id_map[old_id] = bound_right_to_left[old_id]
            _merge_anchor_entity_metadata(
                _entity_by_id(left_entities, bound_right_to_left[old_id]), entity
            )
            continue
        new_id = next_id
        next_id += 1
        right_id_map[old_id] = new_id
        entity["entity_number"] = new_id
        position = entity.get("position")
        if not isinstance(position, dict):
            raise ValueError(f"right entity {old_id} has no position")
        position["x"] = float(position["x"]) + right_offset[0]
        position["y"] = float(position["y"]) + right_offset[1]
        left_entities.append(entity)

    merged_wires = {_wire_tuple(raw) for raw in left_wires}
    for raw in right_wires:
        left_id, left_connector, right_id, right_connector = _wire_tuple(raw)
        merged_wires.add(
            _normalized_wire(
                right_id_map[left_id],
                left_connector,
                right_id_map[right_id],
                right_connector,
            )
        )
    left_bp["wires"] = [list(wire) for wire in sorted(merged_wires)]
    if label is not None:
        left_bp["label"] = label

    bound_left_names = {binding.left for binding in normalized}
    bound_right_names = {binding.right for binding in normalized}
    surviving: list[BoundAnchor] = [
        anchor for anchor in left.anchors if anchor.name not in bound_left_names
    ]
    for anchor in right.anchors:
        if anchor.name in bound_right_names:
            continue
        surviving.append(
            BoundAnchor(
                anchor.spec,
                right_id_map[anchor.entity_number],
                anchor.connector_id,
                (
                    anchor.position[0] + right_offset[0],
                    anchor.position[1] + right_offset[1],
                ),
            )
        )

    _validate_wire_references(left_bp)
    _validate_bound_junctions(left_bp, left, right, normalized, right_id_map)
    return ComposedAnchoredBlueprint(left_bp, tuple(surviving))


def device_as_anchored_blueprint(device: object, *, label: str | None = None) -> AnchoredBlueprint:
    """Adapt an ``ExternalDeviceBlueprint``-like object to the generic anchoring API."""

    protocol = getattr(device, "protocol")
    blueprint = getattr(device, "blueprint")
    ports = getattr(device, "ports")
    anchors: list[BoundAnchor] = []
    for bound in ports:
        spec = bound.spec
        endpoint = bound.endpoint
        anchors.append(
            BoundAnchor(
                AnchorSpec(
                    spec.name,
                    spec.direction,
                    spec.payload_shape,
                    spec.modality,
                    spec.wire,
                    spec.signal,
                ),
                endpoint.entity_number,
                endpoint.connector_id,
                endpoint.position,
            )
        )
    component_label = label or getattr(protocol, "name", "external-device")
    return AnchoredBlueprint(blueprint, tuple(anchors), component_label)


def require_all_anchors_bound(
    component: AnchoredBlueprint,
    bound_names: Iterable[str],
) -> None:
    """Reject a composition plan that leaves required component anchors unbound."""

    bound = set(bound_names)
    missing = [
        anchor.name for anchor in component.anchors if anchor.spec.required and anchor.name not in bound
    ]
    if missing:
        raise ValueError(f"required anchors remain unbound: {sorted(missing)!r}")


def _validate_compatible(left: AnchorSpec, right: AnchorSpec) -> None:
    if left.direction is right.direction:
        raise ValueError(
            f"anchors {left.name!r}/{right.name!r} have the same direction {left.direction.value}"
        )
    if left.payload_shape is not right.payload_shape:
        raise ValueError("anchor payload shapes do not match")
    if left.modality is not right.modality:
        raise ValueError("anchor temporal modalities do not match")
    if left.wire is not right.wire:
        raise ValueError("anchor wire colors do not match")
    if left.signal != right.signal:
        raise ValueError("anchor fixed signals do not match")


def _validate_anchor_entities(component: AnchoredBlueprint) -> None:
    entities = _entities(component.blueprint)
    by_id = {int(entity["entity_number"]): entity for entity in entities}
    for anchor in component.anchors:
        entity = by_id.get(anchor.entity_number)
        if entity is None:
            raise ValueError(f"anchor {anchor.name!r} references missing entity")
        if entity.get("name") != "constant-combinator":
            raise ValueError(f"anchor {anchor.name!r} must bind a constant-combinator terminal")
        position = _position(entity)
        if position != anchor.position:
            raise ValueError(
                f"anchor {anchor.name!r} position metadata {anchor.position!r} "
                f"does not match entity position {position!r}"
            )
        expected_connector = 1 if anchor.spec.wire is WireColor.RED else 2
        if anchor.connector_id != expected_connector:
            raise ValueError(
                f"anchor {anchor.name!r} uses connector {anchor.connector_id}; "
                f"{anchor.spec.wire.value} constant-combinator connector is {expected_connector}"
            )


def _validate_anchor_local_connectivity(component: AnchoredBlueprint, anchor: BoundAnchor) -> None:
    wires = {_wire_tuple(raw) for raw in _wires(component.blueprint)}
    incident = any(
        (wire[0] == anchor.entity_number and wire[1] == anchor.connector_id)
        or (wire[2] == anchor.entity_number and wire[3] == anchor.connector_id)
        for wire in wires
    )
    if incident:
        return
    if anchor.spec.direction is DevicePortDirection.OUTPUT:
        entity = _entity_by_id(_entities(component.blueprint), anchor.entity_number)
        if _constant_emits_signals(entity):
            return
    raise ValueError(
        f"anchor {anchor.name!r} in {component.label!r} is electrically dead on its "
        f"{anchor.spec.wire.value} connector"
    )


def _validate_bound_junctions(
    blueprint: Blueprint,
    left: AnchoredBlueprint,
    right: AnchoredBlueprint,
    bindings: Sequence[AnchorBinding],
    right_id_map: Mapping[int, int],
) -> None:
    wires = {_wire_tuple(raw) for raw in _wires(blueprint)}
    for binding in bindings:
        left_anchor = left.anchor(binding.left)
        right_anchor = right.anchor(binding.right)
        shared_id = left_anchor.entity_number
        connector = left_anchor.connector_id
        incident = [
            wire
            for wire in wires
            if (wire[0] == shared_id and wire[1] == connector)
            or (wire[2] == shared_id and wire[3] == connector)
        ]
        # If one side is a constant source it can legitimately contribute no incident wire; otherwise
        # two component-local incident paths should survive the merge. The local checks above already
        # prove each side independently, so at least one incident path is mandatory in the result.
        if not incident:
            left_entity = _entity_by_id(_entities(left.blueprint), left_anchor.entity_number)
            right_entity = _entity_by_id(_entities(right.blueprint), right_anchor.entity_number)
            if not (_constant_emits_signals(left_entity) or _constant_emits_signals(right_entity)):
                raise ValueError(
                    f"merged anchor {binding.left!r}/{binding.right!r} has no surviving circuit path"
                )
        # Defensive check that a non-anchor right-side neighbor was actually remapped into the result.
        if right_anchor.entity_number not in right_id_map:
            raise ValueError("internal anchor remap was lost")


def _merge_anchor_entity_metadata(target: dict[str, object], source: dict[str, object]) -> None:
    """Preserve useful source configuration when two terminal constant combinators overlap."""

    target_behavior = target.get("control_behavior")
    source_behavior = source.get("control_behavior")
    if target_behavior is not None and source_behavior is not None and target_behavior != source_behavior:
        raise ValueError("overlapping anchors both define incompatible constant-combinator behavior")
    if target_behavior is None and source_behavior is not None:
        target["control_behavior"] = deepcopy(source_behavior)
    target_desc = str(target.get("player_description", ""))
    source_desc = str(source.get("player_description", ""))
    if source_desc and source_desc not in target_desc:
        target["player_description"] = (
            f"{target_desc} | {source_desc}" if target_desc else source_desc
        )


def _constant_emits_signals(entity: Mapping[str, object]) -> bool:
    behavior = entity.get("control_behavior")
    if not isinstance(behavior, Mapping):
        return False
    sections = behavior.get("sections")
    if not isinstance(sections, Mapping):
        return False
    raw_sections = sections.get("sections")
    if not isinstance(raw_sections, list):
        return False
    for section in raw_sections:
        if not isinstance(section, Mapping):
            continue
        filters = section.get("filters")
        if isinstance(filters, list) and filters:
            return True
    return False


def _entities(blueprint: Blueprint) -> list[dict[str, object]]:
    raw = blueprint.setdefault("entities", [])
    if not isinstance(raw, list) or not all(isinstance(entity, dict) for entity in raw):
        raise ValueError("blueprint entities must be dictionaries")
    return raw  # type: ignore[return-value]


def _wires(blueprint: Blueprint) -> list[object]:
    raw = blueprint.setdefault("wires", [])
    if not isinstance(raw, list):
        raise ValueError("blueprint wires must be a list")
    return raw


def _entity_by_id(entities: Sequence[dict[str, object]], entity_number: int) -> dict[str, object]:
    matches = [entity for entity in entities if int(entity["entity_number"]) == entity_number]
    if len(matches) != 1:
        raise ValueError(f"expected entity {entity_number}, found {len(matches)}")
    return matches[0]


def _position(entity: Mapping[str, object]) -> tuple[float, float]:
    position = entity.get("position")
    if not isinstance(position, Mapping):
        raise ValueError(f"entity {entity.get('entity_number')} has no position")
    return float(position["x"]), float(position["y"])


def _wire_tuple(raw: object) -> tuple[int, int, int, int]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise ValueError(f"invalid blueprint wire {raw!r}")
    return tuple(int(value) for value in raw)  # type: ignore[return-value]


def _normalized_wire(
    left: int,
    left_connector: int,
    right: int,
    right_connector: int,
) -> tuple[int, int, int, int]:
    if left > right:
        return right, right_connector, left, left_connector
    return left, left_connector, right, right_connector


def _validate_wire_references(blueprint: Blueprint) -> None:
    ids = {int(entity["entity_number"]) for entity in _entities(blueprint)}
    for raw in _wires(blueprint):
        left, _lc, right, _rc = _wire_tuple(raw)
        if left not in ids or right not in ids:
            raise ValueError(f"wire {raw!r} references a missing entity")
