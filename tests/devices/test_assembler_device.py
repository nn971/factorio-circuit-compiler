from math import hypot

import pytest

from factorio_circuit.devices.assembler import (
    ASSEMBLER_DEVICE_PROTOCOL,
    ASSEMBLER_ENABLE_SIGNAL,
    ASSEMBLER_FINISHED_SIGNAL,
    ASSEMBLER_WORKING_SIGNAL,
    AssemblerDevice,
)
from factorio_circuit.devices._blueprint import decode_blueprint
from factorio_circuit.devices.protocol import DevicePortDirection
from factorio_circuit.ir.physical import WireColor
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality


def _entities(device) -> list[dict[str, object]]:
    raw = device.blueprint["entities"]
    assert isinstance(raw, list)
    assert all(isinstance(entity, dict) for entity in raw)
    return raw  # type: ignore[return-value]


def _one(entities: list[dict[str, object]], name: str) -> dict[str, object]:
    matches = [entity for entity in entities if entity.get("name") == name]
    assert len(matches) == 1
    return matches[0]


def _described(entities: list[dict[str, object]], text: str) -> list[dict[str, object]]:
    return [entity for entity in entities if text in str(entity.get("player_description", ""))]


def test_assembler_protocol_has_typed_stable_ports() -> None:
    assert ASSEMBLER_DEVICE_PROTOCOL.name == "assembler-v3"
    assert [port.name for port in ASSEMBLER_DEVICE_PROTOCOL.ports] == [
        "recipe",
        "enable",
        "requester_demand",
        "ingredients",
        "requester_contents",
        "provider_contents",
        "working",
        "finished",
    ]

    for name in ("recipe", "requester_demand"):
        port = ASSEMBLER_DEVICE_PROTOCOL.port(name)
        assert port.direction is DevicePortDirection.INPUT
        assert port.payload_shape is PayloadShape.VECTOR
        assert port.modality is TemporalModality.LEVEL
        assert port.wire is WireColor.GREEN
        assert port.signal is None

    enable = ASSEMBLER_DEVICE_PROTOCOL.port("enable")
    assert enable.direction is DevicePortDirection.INPUT
    assert enable.payload_shape is PayloadShape.SCALAR
    assert enable.modality is TemporalModality.LEVEL
    assert enable.wire is WireColor.GREEN
    assert enable.signal == ASSEMBLER_ENABLE_SIGNAL

    for name in ("ingredients", "requester_contents", "provider_contents"):
        port = ASSEMBLER_DEVICE_PROTOCOL.port(name)
        assert port.direction is DevicePortDirection.OUTPUT
        assert port.payload_shape is PayloadShape.VECTOR
        assert port.modality is TemporalModality.LEVEL
        assert port.wire is WireColor.RED
        assert port.signal is None

    working = ASSEMBLER_DEVICE_PROTOCOL.port("working")
    assert working.payload_shape is PayloadShape.SCALAR
    assert working.modality is TemporalModality.LEVEL
    assert working.signal == ASSEMBLER_WORKING_SIGNAL

    finished = ASSEMBLER_DEVICE_PROTOCOL.port("finished")
    assert finished.payload_shape is PayloadShape.SCALAR
    assert finished.modality is TemporalModality.EVENT
    assert finished.signal == ASSEMBLER_FINISHED_SIGNAL


def test_assembler_blueprint_contains_logistic_io_and_machine_controls() -> None:
    device = AssemblerDevice().build()
    entities = _entities(device)
    machine = _one(entities, "assembling-machine-3")
    requester = _one(entities, "requester-chest")
    provider = _one(entities, "active-provider-chest")
    inserters = [entity for entity in entities if entity.get("name") == "bulk-inserter"]
    assert len(inserters) == 2

    behavior = machine["control_behavior"]
    assert isinstance(behavior, dict)
    assert behavior["input_networks"] == {"red": False, "green": True}
    assert behavior["output_networks"] == {"red": True, "green": False}
    assert behavior["set_recipe"] is True
    assert behavior["circuit_enabled"] is True
    assert behavior["read_contents"] is False
    assert behavior["read_ingredients"] is True
    assert behavior["read_working"] is True
    assert behavior["read_recipe_finished"] is True

    requester_behavior = requester["control_behavior"]
    assert isinstance(requester_behavior, dict)
    assert requester_behavior["input_networks"] == {"red": False, "green": True}
    assert requester_behavior["output_networks"] == {"red": True, "green": False}
    assert requester_behavior["set_requests"] is True
    assert requester_behavior["read_contents"] is True

    provider_behavior = provider["control_behavior"]
    assert isinstance(provider_behavior, dict)
    assert provider_behavior["input_networks"] == {"red": False, "green": False}
    assert provider_behavior["output_networks"] == {"red": True, "green": False}
    assert provider_behavior["read_contents"] is True


def test_logistic_item_flow_geometry_and_enable_gate() -> None:
    device = AssemblerDevice().build()
    entities = _entities(device)
    input_inserter = _described(entities, "requester -> assembler feeder")
    output_inserter = _described(entities, "assembler -> provider output")
    assert len(input_inserter) == 1
    assert len(output_inserter) == 1

    feeder = input_inserter[0]
    output = output_inserter[0]
    assert feeder["direction"] == 8
    assert output["direction"] == 12

    behavior = feeder["control_behavior"]
    assert isinstance(behavior, dict)
    assert behavior["input_networks"] == {"red": False, "green": True}
    assert behavior["output_networks"] == {"red": False, "green": False}
    assert behavior["circuit_enabled"] is True
    condition = behavior["circuit_condition"]
    assert isinstance(condition, dict)
    assert condition["first_signal"] == {"type": "virtual", "name": "signal-E"}
    assert condition["first_signal_networks"] == {"red": False, "green": True}


def test_commands_and_observations_are_on_declared_colors() -> None:
    device = AssemblerDevice().build()
    for name in ("recipe", "enable", "requester_demand"):
        port = device.port(name)
        assert port.endpoint.wire is WireColor.GREEN
        assert port.endpoint.connector_id == 2
    for name in (
        "ingredients",
        "requester_contents",
        "provider_contents",
        "working",
        "finished",
    ):
        port = device.port(name)
        assert port.endpoint.wire is WireColor.RED
        assert port.endpoint.connector_id == 1


def test_observation_ports_are_isolated() -> None:
    device = AssemblerDevice().build()
    entities = _entities(device)
    descriptions = {str(entity.get("player_description", "")) for entity in entities}
    assert "ASSEMBLER DEVICE isolate requester contents" in descriptions
    assert "ASSEMBLER DEVICE isolate provider contents" in descriptions
    assert "ASSEMBLER DEVICE copy ingredients" in descriptions
    assert "ASSEMBLER DEVICE strip working from ingredients" in descriptions
    assert "ASSEMBLER DEVICE strip finished from ingredients" in descriptions
    assert "ASSEMBLER DEVICE extract working" in descriptions
    assert "ASSEMBLER DEVICE extract finished event" in descriptions

    output_ids = {
        device.port(name).endpoint.entity_number
        for name in (
            "ingredients",
            "requester_contents",
            "provider_contents",
            "working",
            "finished",
        )
    }
    assert len(output_ids) == 5


def test_assembler_blueprint_has_valid_reachable_wires() -> None:
    device = AssemblerDevice().build()
    entities = _entities(device)
    positions = {
        int(entity["entity_number"]): (
            float(entity["position"]["x"]),
            float(entity["position"]["y"]),
        )
        for entity in entities
    }
    wires = device.blueprint["wires"]
    assert isinstance(wires, list)

    ids = set(positions)
    for left, _left_connector, right, _right_connector in wires:
        assert int(left) in ids
        assert int(right) in ids
        left_position = positions[int(left)]
        right_position = positions[int(right)]
        distance = hypot(
            left_position[0] - right_position[0],
            left_position[1] - right_position[1],
        )
        assert distance <= 9.0


def test_assembler_exposes_only_unoccupied_machine_attachment_sides() -> None:
    device = AssemblerDevice().build()
    assert device.attachment("machine_north").position == (8.5, 6.5)
    assert device.attachment("machine_west").position == (6.5, 8.5)
    with pytest.raises(KeyError):
        device.attachment("south")
    with pytest.raises(KeyError):
        device.attachment("east")


def test_assembler_blueprint_string_round_trips() -> None:
    device = AssemblerDevice().build()
    decoded = decode_blueprint(device.blueprint_string())
    assert decoded == device.blueprint


def test_assembler_supports_explicit_module_loadout() -> None:
    modules = ("productivity-module-3",) * 4
    device = AssemblerDevice(modules=modules).build()
    machine = _one(_entities(device), "assembling-machine-3")
    items = machine.get("items")
    assert isinstance(items, list)
    assert len(items) == 1
    plan = items[0]
    assert plan["id"] == {"name": "productivity-module-3", "quality": "normal"}
    assert [entry["stack"] for entry in plan["items"]["in_inventory"]] == [0, 1, 2, 3]

    with pytest.raises(ValueError, match="at most four modules"):
        AssemblerDevice(modules=("speed-module-3",) * 5)


def test_assembler_rejects_invalid_direction() -> None:
    with pytest.raises(ValueError, match="assembler direction"):
        AssemblerDevice(direction=3)
