"""Generate an in-game probe for AssemblerDevice v3 active-provider I/O."""

from __future__ import annotations

from copy import deepcopy

from factorio_circuit.devices import (
    ASSEMBLER_ENABLE_SIGNAL,
    ASSEMBLER_FINISHED_SIGNAL,
    ASSEMBLER_WORKING_SIGNAL,
    AssemblerDevice,
)
from factorio_circuit.devices._blueprint import Blueprint, encode_blueprint
from factorio_circuit.ir.physical import SignalId

IRON_GEAR = SignalId("item", "iron-gear-wheel")
IRON_PLATE = SignalId("item", "iron-plate")


def _signal_json(signal: SignalId) -> dict[str, str]:
    return {"type": signal.kind, "name": signal.name}


def _constant_sections(signal: SignalId, count: int) -> dict[str, object]:
    return {
        "sections": {
            "sections": [
                {
                    "index": 1,
                    "filters": [
                        {
                            "index": 1,
                            "type": signal.kind,
                            "name": signal.name,
                            "quality": "normal",
                            "comparator": "=",
                            "count": count,
                        }
                    ],
                }
            ]
        }
    }


def _lamp(
    entity_number: int,
    x: float,
    y: float,
    signal: SignalId,
    label: str,
) -> dict[str, object]:
    return {
        "entity_number": entity_number,
        "name": "small-lamp",
        "position": {"x": x, "y": y},
        "player_description": label,
        "control_behavior": {
            "circuit_enabled": True,
            "circuit_condition": {
                "first_signal": _signal_json(signal),
                "constant": 0,
                "comparator": ">",
            },
        },
    }


def _port_constant(
    by_id: dict[int, dict[str, object]],
    device,
    port_name: str,
    signal: SignalId,
    count: int,
) -> None:
    endpoint = device.port(port_name).endpoint
    by_id[endpoint.entity_number]["control_behavior"] = _constant_sections(signal, count)


def build_assembler_device_probe_blueprint() -> Blueprint:
    """Seed a gear recipe + 2-plate requester setpoint; enable starts at zero."""

    device = AssemblerDevice(label="AssemblerDevice v3 active-provider probe").build()
    blueprint = deepcopy(device.blueprint)
    entities = blueprint["entities"]
    wires = blueprint["wires"]
    assert isinstance(entities, list)
    assert isinstance(wires, list)

    by_id = {int(entity["entity_number"]): entity for entity in entities}
    _port_constant(by_id, device, "recipe", IRON_GEAR, 1)
    _port_constant(by_id, device, "enable", ASSEMBLER_ENABLE_SIGNAL, 0)
    _port_constant(by_id, device, "requester_demand", IRON_PLATE, 2)

    working = device.port("working").endpoint
    finished = device.port("finished").endpoint
    requester_contents = device.port("requester_contents").endpoint
    provider_contents = device.port("provider_contents").endpoint

    next_id = max(by_id) + 1
    requester_lamp = next_id
    provider_lamp = next_id + 1
    working_lamp = next_id + 2
    finished_lamp = next_id + 3
    entities.extend(
        [
            _lamp(
                requester_lamp,
                22.5,
                8.5,
                IRON_PLATE,
                "PROBE requester_contents has iron plate",
            ),
            _lamp(
                provider_lamp,
                22.5,
                11.5,
                IRON_GEAR,
                "PROBE provider_contents has iron gear",
            ),
            _lamp(
                working_lamp,
                22.5,
                14.5,
                ASSEMBLER_WORKING_SIGNAL,
                "PROBE working (W)",
            ),
            _lamp(
                finished_lamp,
                22.5,
                17.5,
                ASSEMBLER_FINISHED_SIGNAL,
                "PROBE finished pulse (F)",
            ),
        ]
    )
    wires.extend(
        [
            [
                requester_contents.entity_number,
                requester_contents.connector_id,
                requester_lamp,
                1,
            ],
            [
                provider_contents.entity_number,
                provider_contents.connector_id,
                provider_lamp,
                1,
            ],
            [working.entity_number, working.connector_id, working_lamp, 1],
            [finished.entity_number, finished.connector_id, finished_lamp, 1],
        ]
    )
    blueprint["label"] = "AssemblerDevice v3 probe — requester -> gear -> active provider"
    return blueprint


def generate_assembler_device_probe_blueprint_string() -> str:
    return encode_blueprint(build_assembler_device_probe_blueprint())


def main() -> None:
    print(generate_assembler_device_probe_blueprint_string())


if __name__ == "__main__":
    main()
