from __future__ import annotations

import pytest

from factorio_circuit.devices._blueprint import decode_blueprint
from factorio_circuit.devices.protocol import DevicePortDirection
from factorio_circuit.devices.roboport_stock_reader import (
    ROBOPORT_READ_LOGISTICS,
    ROBOPORT_STOCK_READER_PROTOCOL,
    RoboportStockReaderDevice,
    generate_roboport_stock_reader_blueprint_string,
)
from factorio_circuit.ir.physical import WireColor
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality


def _entity(blueprint: dict[str, object], entity_number: int) -> dict[str, object]:
    entities = blueprint["entities"]
    assert isinstance(entities, list)
    return next(
        entity
        for entity in entities
        if isinstance(entity, dict) and entity.get("entity_number") == entity_number
    )


def test_roboport_stock_reader_protocol_and_endpoint_are_stable() -> None:
    device = RoboportStockReaderDevice().build()
    port = device.port("stock")

    assert device.protocol is ROBOPORT_STOCK_READER_PROTOCOL
    assert port.spec.direction is DevicePortDirection.OUTPUT
    assert port.spec.payload_shape is PayloadShape.VECTOR
    assert port.spec.modality is TemporalModality.LEVEL
    assert port.spec.wire is WireColor.RED
    assert port.spec.signal is None
    assert port.endpoint.entity_number == 1
    assert port.endpoint.connector_id == 1
    assert port.endpoint.position == (4.5, 2.5)

    anchor = device.anchored().anchor("stock")
    assert anchor.entity_number == 1
    assert anchor.connector_id == 1
    assert anchor.position == (4.5, 2.5)


def test_roboport_stock_reader_emits_logistics_only_on_red() -> None:
    blueprint = RoboportStockReaderDevice(label="Factory stock").build().blueprint
    roboport = _entity(blueprint, 2)

    assert blueprint["label"] == "Factory stock"
    assert blueprint["wires"] == [[1, 1, 2, 1]]
    assert roboport["name"] == "roboport"
    assert roboport["position"] == {"x": 2.0, "y": 2.0}
    assert roboport["control_behavior"] == {
        "output_networks": {"red": True, "green": False},
        "read_items_mode": ROBOPORT_READ_LOGISTICS,
        "read_robot_stats": False,
    }


def test_roboport_stock_reader_blueprint_string_round_trips() -> None:
    decoded = decode_blueprint(generate_roboport_stock_reader_blueprint_string())
    assert _entity(decoded, 2)["control_behavior"]["read_items_mode"] == 1


def test_roboport_stock_reader_rejects_empty_label() -> None:
    with pytest.raises(ValueError, match="label"):
        RoboportStockReaderDevice(label="")
