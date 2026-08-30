from __future__ import annotations

import pytest

from factorio_circuit.devices._blueprint import decode_blueprint
from factorio_circuit.devices.protocol import DevicePortDirection
from factorio_circuit.devices.pulse_readers import (
    BELT_PULSE_READER_PROTOCOL,
    EVENT_VALID_SIGNAL,
    INSERTER_PULSE_READER_PROTOCOL,
    PULSE_READ_MODE,
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


@pytest.mark.parametrize(
    ("device", "protocol", "prototype", "read_mode_key"),
    [
        (
            TransportBeltPulseReaderDevice(),
            BELT_PULSE_READER_PROTOCOL,
            "transport-belt",
            "circuit_contents_read_mode",
        ),
        (
            InserterPulseReaderDevice(),
            INSERTER_PULSE_READER_PROTOCOL,
            "inserter",
            "circuit_hand_read_mode",
        ),
    ],
)
def test_pulse_reader_protocol_and_physical_source_are_stable(
    device: TransportBeltPulseReaderDevice | InserterPulseReaderDevice,
    protocol,
    prototype: str,
    read_mode_key: str,
) -> None:
    built = device.build()
    payload = built.port("payload")
    valid = built.port("valid")

    assert built.protocol is protocol
    assert payload.spec.direction is DevicePortDirection.OUTPUT
    assert payload.spec.payload_shape is PayloadShape.VECTOR
    assert payload.spec.modality is TemporalModality.EVENT
    assert payload.spec.wire is WireColor.RED
    assert payload.spec.signal is None
    assert payload.endpoint.position == (0.5, 0.5)

    assert valid.spec.direction is DevicePortDirection.OUTPUT
    assert valid.spec.payload_shape is PayloadShape.SCALAR
    assert valid.spec.modality is TemporalModality.EVENT
    assert valid.spec.wire is WireColor.GREEN
    assert valid.spec.signal == EVENT_VALID_SIGNAL
    assert valid.endpoint.position == (0.5, 2.5)

    blueprint = built.blueprint
    sensor = _entity(blueprint, 5)
    assert sensor["name"] == prototype
    assert sensor["control_behavior"] == {
        "output_networks": {"red": True, "green": True},
        "circuit_read_hand_contents": True,
        read_mode_key: PULSE_READ_MODE,
    }


def test_pulse_reader_delays_payload_and_valid_by_equal_one_combinator_stage() -> None:
    blueprint = TransportBeltPulseReaderDevice().build().blueprint
    payload_delay = _entity(blueprint, 3)
    valid_gate = _entity(blueprint, 4)

    assert payload_delay["name"] == "arithmetic-combinator"
    assert payload_delay["control_behavior"]["arithmetic_conditions"] == {
        "first_signal": {"type": "virtual", "name": "signal-each"},
        "second_constant": 0,
        "operation": "+",
        "output_signal": {"type": "virtual", "name": "signal-each"},
    }
    assert valid_gate["name"] == "decider-combinator"
    outputs = valid_gate["control_behavior"]["decider_conditions"]["outputs"]
    assert outputs == [
        {
            "signal": {"type": "virtual", "name": "signal-A"},
            "copy_count_from_input": False,
        }
    ]
    assert blueprint["wires"] == [
        [1, 1, 3, 3],
        [2, 2, 4, 4],
        [3, 1, 5, 1],
        [4, 2, 5, 2],
    ]


def test_pulse_reader_blueprint_strings_round_trip() -> None:
    belt = decode_blueprint(generate_transport_belt_pulse_reader_blueprint_string())
    inserter = decode_blueprint(generate_inserter_pulse_reader_blueprint_string())
    assert _entity(belt, 5)["name"] == "transport-belt"
    assert _entity(inserter, 5)["name"] == "inserter"


@pytest.mark.parametrize("device_type", [TransportBeltPulseReaderDevice, InserterPulseReaderDevice])
def test_pulse_reader_rejects_empty_label(device_type) -> None:
    with pytest.raises(ValueError, match="label"):
        device_type(label="").build()
