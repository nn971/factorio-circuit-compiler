"""Top-edge socket adapter for :class:`AssemblerDevice`.

The core assembler device keeps its proven internal physical layout.  This adapter adds a passive
constant-combinator harness that relocates every typed protocol endpoint onto one top-edge row.  A
compiled controller can then live above the device and bind all ports by exact-overlap anchors
without routing through the machine bay.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Final

from factorio_circuit.devices.protocol import (
    BoundDevicePort,
    DeviceEndpoint,
    DeviceProtocol,
    ExternalDeviceBlueprint,
)
from factorio_circuit.ir.physical import WireColor

_SOCKET_Y: Final = 1.5
_SOCKET_X: Final = {
    "recipe": 1.5,
    "enable": 4.5,
    "requester_demand": 7.5,
    "ingredients": 10.5,
    "requester_contents": 13.5,
    "provider_contents": 16.5,
    "working": 19.5,
    "finished": 22.5,
}

# Relay paths are ordered from the original endpoint toward the top socket.  They are specific to
# the stable AssemblerDevice v3 geometry and deliberately stay outside occupied entity footprints.
_RELAY_PATHS: Final = {
    "recipe": (),
    "enable": ((0.0, 6.0),),
    "requester_demand": ((4.5, 8.5),),
    "ingredients": ((17.0, 3.5),),
    "requester_contents": ((6.0, 12.0), (6.0, 5.0)),
    "provider_contents": ((24.0, 13.0), (23.0, 6.0)),
    "working": ((22.0, 6.5),),
    "finished": ((24.0, 7.5),),
}


def socketize_assembler_device(device: ExternalDeviceBlueprint) -> ExternalDeviceBlueprint:
    """Return ``device`` with all protocol ports re-exported on one top-edge socket row."""

    expected = set(_SOCKET_X)
    actual = {port.name for port in device.ports}
    if actual != expected:
        raise ValueError(
            "assembler socket adapter requires the standard assembler protocol ports: "
            f"expected {sorted(expected)!r}, got {sorted(actual)!r}"
        )

    blueprint = deepcopy(device.blueprint)
    entities = _entities(blueprint)
    wires = _wires(blueprint)
    next_id = max((int(entity["entity_number"]) for entity in entities), default=0) + 1
    endpoints: dict[str, DeviceEndpoint] = {}

    for bound in device.ports:
        name = bound.name
        old = bound.endpoint
        connector = _constant_connector(bound.spec.wire)
        if old.connector_id != connector:
            raise ValueError(
                f"assembler port {name!r} is not bound to a constant-combinator "
                f"{bound.spec.wire.value} connector"
            )

        previous_id = old.entity_number
        previous_connector = old.connector_id
        for relay_index, position in enumerate(_RELAY_PATHS[name], start=1):
            relay_id = next_id
            next_id += 1
            entities.append(
                _terminal(
                    relay_id,
                    position,
                    f"ASSEMBLER SOCKET {name} relay {relay_index}",
                )
            )
            _append_wire(wires, previous_id, previous_connector, relay_id, connector)
            previous_id = relay_id
            previous_connector = connector

        socket_id = next_id
        next_id += 1
        position = (_SOCKET_X[name], _SOCKET_Y)
        entities.append(
            _terminal(
                socket_id,
                position,
                (
                    f"ASSEMBLER SOCKET {name} — {bound.spec.direction.value.upper()} "
                    f"{bound.spec.modality.value} {bound.spec.payload_shape.value}; "
                    f"{bound.spec.wire.value.upper()}"
                ),
            )
        )
        _append_wire(wires, previous_id, previous_connector, socket_id, connector)
        endpoints[name] = DeviceEndpoint(
            socket_id,
            connector,
            bound.spec.wire,
            position,
        )

    blueprint["label"] = f"{blueprint.get('label', 'AssemblerDevice')} — top socket"
    blueprint["wires"] = [list(item) for item in sorted({_wire_tuple(raw) for raw in wires})]
    protocol = DeviceProtocol(f"{device.protocol.name}-top-socket", device.protocol.ports)
    ports = tuple(BoundDevicePort(spec, endpoints[spec.name]) for spec in protocol.ports)
    return ExternalDeviceBlueprint(protocol, blueprint, ports, device.attachments)


def _terminal(entity_number: int, position: tuple[float, float], description: str) -> dict[str, object]:
    return {
        "entity_number": entity_number,
        "name": "constant-combinator",
        "position": {"x": position[0], "y": position[1]},
        "player_description": description,
    }


def _constant_connector(color: WireColor) -> int:
    return 1 if color is WireColor.RED else 2


def _entities(blueprint: dict[str, object]) -> list[dict[str, object]]:
    raw = blueprint.setdefault("entities", [])
    if not isinstance(raw, list) or not all(isinstance(entity, dict) for entity in raw):
        raise ValueError("blueprint entities must be dictionaries")
    return raw  # type: ignore[return-value]


def _wires(blueprint: dict[str, object]) -> list[object]:
    raw = blueprint.setdefault("wires", [])
    if not isinstance(raw, list):
        raise ValueError("blueprint wires must be a list")
    return raw


def _append_wire(
    wires: list[object],
    left: int,
    left_connector: int,
    right: int,
    right_connector: int,
) -> None:
    wires.append(list(_normalized_wire(left, left_connector, right, right_connector)))


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
