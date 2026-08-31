from __future__ import annotations

import pytest

from factorio_circuit.devices._blueprint import decode_blueprint
from factorio_circuit.devices.protocol import DevicePortDirection
from factorio_circuit.devices.train_stop import (
    TRAIN_PRIORITY_SIGNAL,
    TRAIN_STOP_PROTOCOL,
    TRAIN_STOPPED_SIGNAL,
    TRAINS_COUNT_SIGNAL,
    TRAINS_LIMIT_SIGNAL,
    TrainStopDevice,
    generate_train_stop_blueprint_string,
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


def test_train_stop_protocol_separates_command_and_status_buses() -> None:
    device = TrainStopDevice().build()
    commands = device.port("commands")
    status = device.port("status")

    assert device.protocol is TRAIN_STOP_PROTOCOL

    assert commands.spec.direction is DevicePortDirection.INPUT
    assert commands.spec.payload_shape is PayloadShape.VECTOR
    assert commands.spec.modality is TemporalModality.LEVEL
    assert commands.spec.wire is WireColor.GREEN
    assert commands.spec.signal is None
    assert commands.endpoint.entity_number == 1
    assert commands.endpoint.connector_id == 2
    assert commands.endpoint.position == (0.5, 0.5)

    assert status.spec.direction is DevicePortDirection.OUTPUT
    assert status.spec.payload_shape is PayloadShape.VECTOR
    assert status.spec.modality is TemporalModality.LEVEL
    assert status.spec.wire is WireColor.RED
    assert status.spec.signal is None
    assert status.endpoint.entity_number == 2
    assert status.endpoint.connector_id == 1
    assert status.endpoint.position == (0.5, 2.5)


def test_train_stop_blueprint_uses_green_inputs_and_red_outputs() -> None:
    blueprint = (
        TrainStopDevice(
            label="F4 train stop",
            station="Provider station",
            direction=4,
        )
        .build()
        .blueprint
    )
    train_stop = _entity(blueprint, 3)

    assert blueprint["label"] == "F4 train stop"
    assert blueprint["wires"] == [[1, 2, 3, 2], [2, 1, 3, 1]]
    assert train_stop["name"] == "train-stop"
    assert train_stop["position"] == {"x": 3.5, "y": 1.0}
    assert train_stop["direction"] == 4
    assert train_stop["station"] == "Provider station"
    assert train_stop["control_behavior"] == {
        "input_networks": {"red": False, "green": True},
        "output_networks": {"red": True, "green": False},
        "send_to_train": True,
        "read_from_train": True,
        "read_stopped_train": True,
        "train_stopped_signal": {"type": "virtual", "name": "signal-T"},
        "set_trains_limit": True,
        "trains_limit_signal": {"type": "virtual", "name": "signal-L"},
        "read_trains_count": True,
        "trains_count_signal": {"type": "virtual", "name": "signal-C"},
        "set_priority": True,
        "priority_signal": {"type": "virtual", "name": "signal-P"},
    }

    assert TRAIN_STOPPED_SIGNAL.name == "signal-T"
    assert TRAINS_COUNT_SIGNAL.name == "signal-C"
    assert TRAINS_LIMIT_SIGNAL.name == "signal-L"
    assert TRAIN_PRIORITY_SIGNAL.name == "signal-P"


def test_train_stop_blueprint_string_round_trips() -> None:
    decoded = decode_blueprint(
        generate_train_stop_blueprint_string(station="Round-trip station", direction=8)
    )
    train_stop = _entity(decoded, 3)
    assert train_stop["station"] == "Round-trip station"
    assert train_stop["direction"] == 8
    assert train_stop["control_behavior"]["set_priority"] is True


def test_train_stop_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="label"):
        TrainStopDevice(label="")
    with pytest.raises(ValueError, match="station"):
        TrainStopDevice(station="")
    with pytest.raises(ValueError, match="direction"):
        TrainStopDevice(direction=2)
