"""Serialize mixed compiler/device layouts while preserving opaque Factorio entity payloads."""

from __future__ import annotations

import base64
from copy import deepcopy
import json
import zlib
from typing import Any

from factorio_circuit.blueprint.layout_encode import layout_to_blueprint_json
from factorio_circuit.ir.physical import (
    OpaqueDualConnectorEntity,
    OpaqueSingleConnectorEntity,
    PhysicalCircuit,
)
from factorio_circuit.synthesis.layout import Layout

OpaqueEntity = OpaqueSingleConnectorEntity | OpaqueDualConnectorEntity


def layout_to_blueprint_json_with_opaque(layout: Layout) -> dict[str, Any]:
    """Serialize a layout containing raw reusable-device entities.

    Ordinary compiler entities and relays are delegated to the normal serializer. Opaque entities
    are then restored from their preserved blueprint payloads at the final physical coordinates.
    Root-level routed wires are already serialized by the normal path and may refer to either kind.
    """

    opaque = [
        entity
        for entity in layout.circuit.entities
        if isinstance(entity, (OpaqueSingleConnectorEntity, OpaqueDualConnectorEntity))
    ]
    ordinary = [
        entity
        for entity in layout.circuit.entities
        if not isinstance(entity, (OpaqueSingleConnectorEntity, OpaqueDualConnectorEntity))
    ]
    ordinary_circuit = PhysicalCircuit(
        layout.circuit.name,
        entities=ordinary,
        connections=list(layout.circuit.connections),
        inputs=list(layout.circuit.inputs),
        outputs=list(layout.circuit.outputs),
    )
    ordinary_layout = Layout(
        circuit=ordinary_circuit,
        positions=layout.positions,
        relays=layout.relays,
        wires=layout.wires,
        signal_allocation=layout.signal_allocation,
        net_colors=layout.net_colors,
        net_groups=layout.net_groups,
    )
    result = layout_to_blueprint_json(ordinary_layout)
    blueprint = result["blueprint"]
    entities = blueprint.setdefault("entities", [])
    for entity in opaque:
        x, y = layout.positions[entity.id]
        payload = deepcopy(entity.blueprint_fields)
        payload["entity_number"] = entity.id
        payload["name"] = entity.prototype
        payload["position"] = {"x": x, "y": y}
        entities.append(payload)
    entities.sort(key=lambda item: int(item["entity_number"]))
    return result


def encode_layout_blueprint_string_with_opaque(layout: Layout) -> str:
    """Encode a mixed compiler/device layout as a Factorio import string."""

    payload = json.dumps(
        layout_to_blueprint_json_with_opaque(layout),
        separators=(",", ":"),
    ).encode()
    return "0" + base64.b64encode(zlib.compress(payload, level=9)).decode("ascii")


__all__ = [
    "encode_layout_blueprint_string_with_opaque",
    "layout_to_blueprint_json_with_opaque",
]
