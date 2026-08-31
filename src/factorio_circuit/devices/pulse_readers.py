"""Reusable one-tick item pulse readers for Event integration.

Factorio belt and inserter circuit readers can emit item contents in pulse mode. The compiler's
physical Event ABI needs the item payload and its one-tick valid token to be present on the same
physical tick. Each F3 reader therefore duplicates the native pulse onto RED and GREEN and gives
both paths exactly one combinator tick of latency:

* RED -> arithmetic ``each + 0`` -> Event vector payload;
* GREEN -> decider ``anything > 0`` -> fixed ``signal-V`` valid token.

The equal-latency paths preserve occurrence alignment. No pulse-to-Level latch is inserted inside
the device.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from factorio_circuit.devices._blueprint import Blueprint, encode_blueprint
from factorio_circuit.devices.protocol import (
    BoundDevicePort,
    DeviceEndpoint,
    DevicePortDirection,
    DevicePortSpec,
    DeviceProtocol,
    ExternalDeviceBlueprint,
)
from factorio_circuit.ir.physical import SignalId, WireColor
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality

FACTORIO_BLUEPRINT_VERSION: Final = 562954249306113
RED_CONNECTOR: Final = 1
GREEN_CONNECTOR: Final = 2
RED_OUTPUT_CONNECTOR: Final = 3
GREEN_OUTPUT_CONNECTOR: Final = 4
PULSE_READ_MODE: Final = 0
PULSE_VALID_SIGNAL: Final = SignalId("virtual", "signal-V")
_ANYTHING: Final = SignalId("virtual", "signal-anything")
_EACH: Final = SignalId("virtual", "signal-each")

PULSE_READER_PROTOCOL: Final = DeviceProtocol(
    "item-pulse-reader-v1",
    (
        DevicePortSpec(
            "items",
            DevicePortDirection.OUTPUT,
            PayloadShape.VECTOR,
            TemporalModality.EVENT,
            WireColor.RED,
        ),
        DevicePortSpec(
            "valid",
            DevicePortDirection.OUTPUT,
            PayloadShape.SCALAR,
            TemporalModality.LEVEL,
            WireColor.GREEN,
            PULSE_VALID_SIGNAL,
        ),
    ),
)


def _signal_json(signal: SignalId) -> dict[str, str]:
    return {"type": signal.kind, "name": signal.name}


def _networks(*, red: bool, green: bool) -> dict[str, bool]:
    return {"red": red, "green": green}


def _dock(entity_number: int, position: tuple[float, float], description: str) -> dict[str, object]:
    return {
        "entity_number": entity_number,
        "name": "constant-combinator",
        "position": {"x": position[0], "y": position[1]},
        "player_description": description,
    }


def _payload_delay() -> dict[str, object]:
    return {
        "entity_number": 3,
        "name": "arithmetic-combinator",
        "position": {"x": 2.0, "y": 0.5},
        "direction": 4,
        "player_description": "PULSE READER align Event payload by one tick",
        "control_behavior": {
            "arithmetic_conditions": {
                "first_signal": _signal_json(_EACH),
                "second_constant": 0,
                "operation": "+",
                "output_signal": _signal_json(_EACH),
            }
        },
    }


def _valid_detector() -> dict[str, object]:
    return {
        "entity_number": 4,
        "name": "decider-combinator",
        "position": {"x": 2.0, "y": 2.5},
        "direction": 4,
        "player_description": "PULSE READER derive aligned one-tick Event valid token",
        "control_behavior": {
            "decider_conditions": {
                "conditions": [
                    {
                        "first_signal": _signal_json(_ANYTHING),
                        "first_signal_networks": _networks(red=False, green=True),
                        "constant": 0,
                        "comparator": ">",
                    }
                ],
                "outputs": [
                    {
                        "signal": _signal_json(PULSE_VALID_SIGNAL),
                        "copy_count_from_input": False,
                    }
                ],
            }
        },
    }


def _blueprint(
    *,
    label: str,
    reader_name: str,
    reader_description: str,
    reader_control_behavior: dict[str, object],
    reader_direction: int | None,
) -> Blueprint:
    if not label:
        raise ValueError("pulse reader label must be non-empty")
    reader: dict[str, object] = {
        "entity_number": 5,
        "name": reader_name,
        "position": {"x": 4.5, "y": 1.5},
        "player_description": reader_description,
        "control_behavior": reader_control_behavior,
    }
    if reader_direction is not None:
        reader["direction"] = reader_direction
    return {
        "item": "blueprint",
        "label": label,
        "version": FACTORIO_BLUEPRINT_VERSION,
        "icons": [{"signal": {"name": reader_name}, "index": 1}],
        "entities": [
            _dock(1, (0.5, 0.5), "PULSE READER PORT items — OUTPUT Event vector; RED"),
            _dock(
                2,
                (0.5, 2.5),
                "PULSE READER PORT valid — OUTPUT Level scalar signal-V; GREEN",
            ),
            _payload_delay(),
            _valid_detector(),
            reader,
        ],
        "wires": [
            [1, RED_CONNECTOR, 3, RED_OUTPUT_CONNECTOR],
            [2, GREEN_CONNECTOR, 4, GREEN_OUTPUT_CONNECTOR],
            [3, RED_CONNECTOR, 5, RED_CONNECTOR],
            [4, GREEN_CONNECTOR, 5, GREEN_CONNECTOR],
        ],
    }


def _external_device(blueprint: Blueprint) -> ExternalDeviceBlueprint:
    return ExternalDeviceBlueprint(
        PULSE_READER_PROTOCOL,
        blueprint,
        (
            BoundDevicePort(
                PULSE_READER_PROTOCOL.port("items"),
                DeviceEndpoint(1, RED_CONNECTOR, WireColor.RED, (0.5, 0.5)),
            ),
            BoundDevicePort(
                PULSE_READER_PROTOCOL.port("valid"),
                DeviceEndpoint(2, GREEN_CONNECTOR, WireColor.GREEN, (0.5, 2.5)),
            ),
        ),
    )


@dataclass(frozen=True, slots=True)
class TransportBeltPulseReaderDevice:
    """Read one transport belt's item contents as aligned one-tick vector Events."""

    label: str = "Transport belt item pulse reader"
    direction: int | None = None

    def __post_init__(self) -> None:
        if not self.label:
            raise ValueError("transport belt pulse reader label must be non-empty")
        if self.direction is not None and self.direction not in {0, 4, 8, 12}:
            raise ValueError("transport belt direction must be one of 0, 4, 8, 12, or None")

    def build(self) -> ExternalDeviceBlueprint:
        return _external_device(
            build_transport_belt_pulse_reader_blueprint(
                label=self.label,
                direction=self.direction,
            )
        )


def build_transport_belt_pulse_reader_blueprint(
    *,
    label: str = "Transport belt item pulse reader",
    direction: int | None = None,
) -> Blueprint:
    config = TransportBeltPulseReaderDevice(label=label, direction=direction)
    return _blueprint(
        label=config.label,
        reader_name="transport-belt",
        reader_description="TRANSPORT BELT item pulse source",
        reader_control_behavior={
            "output_networks": _networks(red=True, green=True),
            "circuit_read_hand_contents": True,
            "circuit_contents_read_mode": PULSE_READ_MODE,
        },
        reader_direction=config.direction,
    )


def generate_transport_belt_pulse_reader_blueprint_string(
    *,
    label: str = "Transport belt item pulse reader",
    direction: int | None = None,
) -> str:
    return encode_blueprint(
        build_transport_belt_pulse_reader_blueprint(label=label, direction=direction)
    )


@dataclass(frozen=True, slots=True)
class InserterPulseReaderDevice:
    """Read one inserter's held-item transfers as aligned one-tick vector Events."""

    label: str = "Inserter item pulse reader"
    prototype: str = "inserter"
    direction: int | None = None

    def __post_init__(self) -> None:
        if not self.label:
            raise ValueError("inserter pulse reader label must be non-empty")
        if not self.prototype:
            raise ValueError("inserter pulse reader prototype must be non-empty")
        if self.direction is not None and self.direction not in {0, 4, 8, 12}:
            raise ValueError("inserter direction must be one of 0, 4, 8, 12, or None")

    def build(self) -> ExternalDeviceBlueprint:
        return _external_device(
            build_inserter_pulse_reader_blueprint(
                label=self.label,
                prototype=self.prototype,
                direction=self.direction,
            )
        )


def build_inserter_pulse_reader_blueprint(
    *,
    label: str = "Inserter item pulse reader",
    prototype: str = "inserter",
    direction: int | None = None,
) -> Blueprint:
    config = InserterPulseReaderDevice(
        label=label,
        prototype=prototype,
        direction=direction,
    )
    return _blueprint(
        label=config.label,
        reader_name=config.prototype,
        reader_description="INSERTER held-item pulse source",
        reader_control_behavior={
            "output_networks": _networks(red=True, green=True),
            "circuit_read_hand_contents": True,
            "circuit_hand_read_mode": PULSE_READ_MODE,
        },
        reader_direction=config.direction,
    )


def generate_inserter_pulse_reader_blueprint_string(
    *,
    label: str = "Inserter item pulse reader",
    prototype: str = "inserter",
    direction: int | None = None,
) -> str:
    return encode_blueprint(
        build_inserter_pulse_reader_blueprint(
            label=label,
            prototype=prototype,
            direction=direction,
        )
    )


__all__ = [
    "PULSE_READ_MODE",
    "PULSE_READER_PROTOCOL",
    "PULSE_VALID_SIGNAL",
    "InserterPulseReaderDevice",
    "TransportBeltPulseReaderDevice",
    "build_inserter_pulse_reader_blueprint",
    "build_transport_belt_pulse_reader_blueprint",
    "generate_inserter_pulse_reader_blueprint_string",
    "generate_transport_belt_pulse_reader_blueprint_string",
]
