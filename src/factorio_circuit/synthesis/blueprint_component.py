"""Import an existing Factorio blueprint as fixed-capable physical layout objects.

This bridge is intentionally explicit about prototype geometry.  The compiler does not carry a
hidden vanilla/mod prototype-size database: a caller importing a reusable device supplies the
collision half-extent and connector shape for every prototype that may occur in that blueprint.
The raw entity payload is retained for serialization while root-level circuit wires become ordinary
``PhysicalCircuit`` connections, so D1/D2/D3 can reason about the device as real physical objects.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from factorio_circuit.ir.physical import (
    Connector,
    OpaqueDualConnectorEntity,
    OpaqueSingleConnectorEntity,
    PhysicalCircuit,
    WireColor,
    WireConnection,
    WireEndpoint,
)
from factorio_circuit.synthesis.layout import Layout, LayoutWire
from factorio_circuit.synthesis.placement import Position

_EPSILON = 1e-9


class BlueprintConnectorShape(StrEnum):
    """Circuit connector topology exposed by one imported Factorio prototype."""

    SINGLE = "single"
    INPUT_OUTPUT = "input-output"


@dataclass(frozen=True, slots=True)
class BlueprintEntityPhysicalSpec:
    """Caller-supplied physical facts required to import one prototype safely."""

    half_extent: tuple[float, float]
    connector_shape: BlueprintConnectorShape = BlueprintConnectorShape.SINGLE

    def __post_init__(self) -> None:
        if self.half_extent[0] <= 0.0 or self.half_extent[1] <= 0.0:
            raise ValueError("blueprint entity half-extents must be positive")


@dataclass(frozen=True, slots=True)
class ImportedBlueprintLayout:
    """Physical layout plus exact source-object geometry retained by the importer."""

    layout: Layout
    half_extents: tuple[tuple[int, tuple[float, float]], ...]

    @property
    def entity_half_extents(self) -> dict[int, tuple[float, float]]:
        return dict(self.half_extents)


def import_blueprint_layout(
    blueprint: Mapping[str, Any],
    *,
    prototype_specs: Mapping[str, BlueprintEntityPhysicalSpec],
    name: str | None = None,
) -> ImportedBlueprintLayout:
    """Convert one ordinary blueprint body into an exact, initially relay-free ``Layout``.

    ``blueprint`` is the inner Factorio blueprint object (the mapping containing ``entities`` and
    optional root-level ``wires``), not the outer ``{"blueprint": ...}`` encoding wrapper.
    Entity numbers and root-level connector ids are preserved exactly.
    """

    source_entities = blueprint.get("entities", ())
    if not isinstance(source_entities, (list, tuple)):
        raise ValueError("blueprint entities must be a sequence")

    entities: list[OpaqueSingleConnectorEntity | OpaqueDualConnectorEntity] = []
    positions: dict[int, Position] = {}
    half_extents: dict[int, tuple[float, float]] = {}
    connector_shapes: dict[int, BlueprintConnectorShape] = {}

    for source in source_entities:
        if not isinstance(source, dict):
            raise ValueError("blueprint entity entries must be mappings")
        entity_id = source.get("entity_number")
        prototype = source.get("name")
        raw_position = source.get("position")
        if not isinstance(entity_id, int) or entity_id <= 0:
            raise ValueError("blueprint entities require positive integer entity_number values")
        if entity_id in positions:
            raise ValueError(f"blueprint contains duplicate entity number {entity_id}")
        if not isinstance(prototype, str) or not prototype:
            raise ValueError(f"blueprint entity {entity_id} has no prototype name")
        if not isinstance(raw_position, dict):
            raise ValueError(f"blueprint entity {entity_id} has no position mapping")
        x = raw_position.get("x")
        y = raw_position.get("y")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            raise ValueError(f"blueprint entity {entity_id} has a non-numeric position")
        try:
            spec = prototype_specs[prototype]
        except KeyError as exc:
            raise ValueError(
                f"blueprint prototype {prototype!r} has no explicit physical specification"
            ) from exc

        preserved = deepcopy(source)
        preserved.pop("entity_number", None)
        preserved.pop("position", None)
        preserved.pop("name", None)
        position = (float(x), float(y))
        entity: OpaqueSingleConnectorEntity | OpaqueDualConnectorEntity
        if spec.connector_shape is BlueprintConnectorShape.SINGLE:
            entity = OpaqueSingleConnectorEntity(
                entity_id,
                prototype,
                preserved,
                physical_half_extent=spec.half_extent,
            )
        else:
            entity = OpaqueDualConnectorEntity(
                entity_id,
                prototype,
                preserved,
                physical_half_extent=spec.half_extent,
            )
        entities.append(entity)
        positions[entity_id] = position
        half_extents[entity_id] = spec.half_extent
        connector_shapes[entity_id] = spec.connector_shape

    _validate_source_clearance(positions, half_extents)

    connections: list[WireConnection] = []
    layout_wires: list[LayoutWire] = []
    source_wires = blueprint.get("wires", ())
    if not isinstance(source_wires, (list, tuple)):
        raise ValueError("blueprint wires must be a sequence")
    for raw_wire in source_wires:
        if not isinstance(raw_wire, (list, tuple)) or len(raw_wire) != 4:
            raise ValueError("blueprint wire entries must contain four integers")
        if not all(isinstance(value, int) for value in raw_wire):
            raise ValueError("blueprint wire entries must contain four integers")
        left_id, left_connector_id, right_id, right_connector_id = raw_wire
        if left_id not in positions or right_id not in positions:
            raise ValueError("blueprint wire refers to an unknown entity number")
        left_color, left_connector = _decode_connector(
            left_id,
            left_connector_id,
            connector_shapes[left_id],
        )
        right_color, right_connector = _decode_connector(
            right_id,
            right_connector_id,
            connector_shapes[right_id],
        )
        if left_color is not right_color:
            raise ValueError("blueprint wire connects red and green connector ids")
        connections.append(
            WireConnection(
                WireEndpoint(left_id, left_connector),
                WireEndpoint(right_id, right_connector),
                left_color,
            )
        )
        layout_wires.append(
            LayoutWire(
                left_id,
                left_connector_id,
                right_id,
                right_connector_id,
                left_color,
            )
        )

    circuit = PhysicalCircuit(
        name or str(blueprint.get("label") or "Imported blueprint component"),
        entities=list(entities),
        connections=connections,
    )
    layout = Layout(
        circuit=circuit,
        positions=positions,
        relays=(),
        wires=tuple(layout_wires),
        signal_allocation=(),
        net_colors=(),
    )
    return ImportedBlueprintLayout(layout, tuple(sorted(half_extents.items())))


def _decode_connector(
    entity_id: int,
    connector_id: int,
    shape: BlueprintConnectorShape,
) -> tuple[WireColor, Connector]:
    if connector_id <= 0:
        raise ValueError(f"entity {entity_id} has invalid circuit connector id {connector_id}")
    color = WireColor.RED if connector_id % 2 else WireColor.GREEN
    red_connector_id = connector_id if color is WireColor.RED else connector_id - 1
    if shape is BlueprintConnectorShape.SINGLE:
        if red_connector_id != 1:
            raise ValueError(
                f"single-connector entity {entity_id} cannot use connector id {connector_id}"
            )
        return color, Connector.SINGLE
    if red_connector_id == 1:
        return color, Connector.INPUT
    if red_connector_id == 3:
        return color, Connector.OUTPUT
    raise ValueError(f"input/output entity {entity_id} cannot use connector id {connector_id}")


def _validate_source_clearance(
    positions: Mapping[int, Position],
    half_extents: Mapping[int, tuple[float, float]],
) -> None:
    ids = sorted(positions)
    for index, left_id in enumerate(ids):
        left = positions[left_id]
        left_half = half_extents[left_id]
        for right_id in ids[index + 1 :]:
            right = positions[right_id]
            right_half = half_extents[right_id]
            if (
                abs(left[0] - right[0]) < left_half[0] + right_half[0] - _EPSILON
                and abs(left[1] - right[1]) < left_half[1] + right_half[1] - _EPSILON
            ):
                raise ValueError(
                    f"blueprint physical entities {left_id} and {right_id} overlap under the "
                    "declared prototype geometry"
                )


__all__ = [
    "BlueprintConnectorShape",
    "BlueprintEntityPhysicalSpec",
    "ImportedBlueprintLayout",
    "import_blueprint_layout",
]
