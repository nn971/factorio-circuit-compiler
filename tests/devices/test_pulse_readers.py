from __future__ import annotations

import pytest

from factorio_circuit.devices._blueprint import decode_blueprint
from factorio_circuit.devices.protocol import DevicePortDirection
from factorio_circuit.devices.pulse_readers import (
    PULSE_READ_MODE,
    PULSE_READER_PROTOCOL,
    PULSE_VALID_SIGNAL,
    InserterPulseReaderDevice,
    TransportBeltPulseReaderDevice,
    generate_inserter_pulse_reader_blueprint_string,
    generate_transport_belt_pulse_reader_blueprint_string,
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


def test_pulse_reader_protocol_matches_external_event_abi() -> None:
    device = TransportBeltPulseReaderDevice().build()
    items = device.port("items")
    valid = device.port("valid")

    assert device.protocol is PULSE_READER_PROTOCOL
    assert items.spec.direction is DevicePortDirection.OUTPUT
    assert items.spec.payload_shape is PayloadShape.VECTOR
    assert items.spec.modality is TemporalModality.EVENT
    assert items.spec.wire is WireColor.RED
    assert items.spec.signal is None
    assert items.endpoint.entity_number == 1
    assert items.endpoint.connector_id == 1
    assert items.endpoint.position == (0.5, 0.5)

    assert valid.spec.direction is DevicePortDirection.OUTPUT
    assert valid.spec.payload_shape is PayloadShape.SCALAR
    assert valid.spec.modality is TemporalModality.LEVEL
    assert valid.spec.wire is WireColor.GREEN
    assert valid.spec.signal == PULSE_VALID_SIGNAL
    assert valid.endpoint.entity_number == 2
    assert valid.endpoint.connector_id == 2
    assert valid.endpoint.position == (0.5, 2.5)


def test_transport_belt_reader_aligns_payload_and_valid_by_one_tick() -> None:
    blueprint = TransportBeltPulseReaderDevice(direction=4).build().blueprint
    payload_delay = _entity(blueprint, 3)
    detector = _entity(blueprint, 4)
    reader = _entity(blueprint, 5)

    assert reader["name"] == "transport-belt"
    assert reader["direction"] == 4
    assert reader["control_behavior"] == {
        "output_networks": {"red": True, "green": True},
        "circuit_read_hand_contents": True,
        "circuit_contents_read_mode": PULSE_READ_MODE,
    }
    assert blueprint["wires"] == [
        [1, 1, 3, 3],
        [2, 2, 4, 4],
        [3, 1, 5, 1],
        [4, 2, 5, 2],
    ]

    arithmetic = payload_delay["control_behavior"]["arithmetic_conditions"]
    assert arithmetic == {
        "first_signal": {"type": "virtual", "name": "signal-each"},
        "second_constant": 0,
        "operation": "+",
        "output_signal": {"type": "virtual", "name": "signal-each"},
    }

    conditions = detector["control_behavior"]["decider_conditions"]
    assert conditions["conditions"][0]["first_signal"]["name"] == "signal-anything"
    assert conditions["conditions"][0]["first_signal_networks"] == {
        "red": False,
        "green": True,
    }
    assert conditions["outputs"][0]["signal"] == {
        "type": "virtual",
        "name": "signal-V",
    }


def test_inserter_reader_uses_native_hand_pulse_mode_on_both_input_colors() -> None:
    blueprint = InserterPulseReaderDevice(prototype="bulk-inserter", direction=8).build().blueprint
    reader = _entity(blueprint, 5)

    assert reader["name"] == "bulk-inserter"
    assert reader["direction"] == 8
    assert reader["control_behavior"] == {
        "output_networks": {"red": True, "green": True},
        "circuit_read_hand_contents": True,
        "circuit_hand_read_mode": PULSE_READ_MODE,
    }


def test_pulse_reader_blueprint_strings_round_trip() -> None:
    belt = decode_blueprint(generate_transport_belt_pulse_reader_blueprint_string())
    inserter = decode_blueprint(generate_inserter_pulse_reader_blueprint_string())

    assert _entity(belt, 5)["control_behavior"]["circuit_contents_read_mode"] == 0
    assert _entity(inserter, 5)["control_behavior"]["circuit_hand_read_mode"] == 0


def test_pulse_reader_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="label"):
        TransportBeltPulseReaderDevice(label="")
    with pytest.raises(ValueError, match="direction"):
        InserterPulseReaderDevice(direction=2)
