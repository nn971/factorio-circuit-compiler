"""Reusable transport-belt and inserter pulse readers for the physical Event ABI.

Factorio's belt/inserter pulse modes already produce a one-tick item vector.  The compiler's Event
ABI additionally requires an explicit one-tick valid token, so each device duplicates the raw pulse
onto two wire colors and delays both paths by one combinator tick:

* RED -> arithmetic ``each + 0`` -> ``payload`` Event vector;
* GREEN -> decider ``anything > 0`` -> fixed ``signal-A`` -> ``valid`` Event scalar.

The equal one-tick paths keep payload and valid aligned.  The reader therefore represents a real
semantic occurrence boundary rather than merely relabelling a transient Level vector as Event.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

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
EVENT_VALID_SIGNAL: Final = SignalId("virtual", "signal-A")
RED_CONNECTOR: Final = 1
GREEN_CONNECTOR: Final = 2
ARITHMETIC_RED_OUTPUT: Final = 3
DECIDER_GREEN_OUTPUT: Final = 4
PULSE_READ_MODE: Final = 0


def _protocol(name: str) -> DeviceProtocol:
    return DeviceProtocol(
        name,
        (
            DevicePortSpec(
                "payload",
                DevicePortDirection.OUTPUT,
                PayloadShape.VECTOR,
                TemporalModality.EVENT,
                WireColor.RED,
            ),
            DevicePortSpec(
                "valid",
                DevicePortDirection.OUTPUT,
                PayloadShape.SCALAR,
                TemporalModality.EVENT,
                WireColor.GREEN,
                EVENT_VALID_SIGNAL,
            ),
        ),
    )


BELT_PULSE_READER_PROTOCOL: Final = _protocol("transport-belt-pulse-reader-v1")
INSERTER_PULSE_READER_PROTOCOL: Final = _protocol("inserter-pulse-reader-v1")
type PulseReaderKind = Literal["transport-belt", "inserter"]


def _signal_json(signal: SignalId) -> dict[str, str]:
    return {"type": signal.kind, "name": signal.name}


def _payload_delay_behavior() -> dict[str, object]:
    each = {"type": "virtual", "name": "signal-each"}
    return {
        "arithmetic_conditions": {
            "first_signal": each,
            "second_constant": 0,
            "operation": "+",
            "output_signal": each,
        }
    }


def _valid_behavior() -> dict[str, object]:
    return {
        "decider_conditions": {
            "conditions": [
                {
                    "first_signal": {"type": "virtual", "name": "signal-anything"},
                    "constant": 0,
                    "comparator": ">",
                }
            ],
            "outputs": [
                {
                    "signal": _signal_json(EVENT_VALID_SIGNAL),
                    "copy_count_from_input": False,
                }
            ],
        }
    }


def _reader_control_behavior(kind: PulseReaderKind) -> dict[str, object]:
    common: dict[str, object] = {
        "output_networks": {"red": True, "green": True},
        "circuit_read_hand_contents": True,
    }
    if kind == "transport-belt":
        common["circuit_contents_read_mode"] = PULSE_READ_MODE
    else:
        common["circuit_hand_read_mode"] = PULSE_READ_MODE
    return common


def _build_pulse_reader_blueprint(*, kind: PulseReaderKind, label: str) -> Blueprint:
    if not label:
        raise ValueError("pulse reader label must be non-empty")
    protocol_name = "BELT" if kind == "transport-belt" else "INSERTER"
    return {
        "item": "blueprint",
        "label": label,
        "version": FACTORIO_BLUEPRINT_VERSION,
        "icons": [{"signal": {"name": kind}, "index": 1}],
        "entities": [
            {
                "entity_number": 1,
                "name": "constant-combinator",
                "position": {"x": 0.5, "y": 0.5},
                "player_description": (
                    f"{protocol_name} PULSE PORT payload — OUTPUT Event vector; RED"
                ),
            },
            {
                "entity_number": 2,
                "name": "constant-combinator",
                "position": {"x": 0.5, "y": 2.5},
                "player_description": (
                    f"{protocol_name} PULSE PORT valid — OUTPUT Event scalar signal-A; GREEN"
                ),
            },
            {
                "entity_number": 3,
                "name": "arithmetic-combinator",
                "position": {"x": 2.0, "y": 0.5},
                "direction": 4,
                "player_description": f"{protocol_name} PULSE align payload by one tick",
                "control_behavior": _payload_delay_behavior(),
            },
            {
                "entity_number": 4,
                "name": "decider-combinator",
                "position": {"x": 2.0, "y": 2.5},
                "direction": 4,
                "player_description": f"{protocol_name} PULSE derive aligned valid token",
                "control_behavior": _valid_behavior(),
            },
            {
                "entity_number": 5,
                "name": kind,
                "position": {"x": 4.5, "y": 1.5},
                "direction": 4,
                "player_description": f"{protocol_name} PULSE physical transfer sensor",
                "control_behavior": _reader_control_behavior(kind),
            },
        ],
        "wires": [
            [1, RED_CONNECTOR, 3, ARITHMETIC_RED_OUTPUT],
            [2, GREEN_CONNECTOR, 4, DECIDER_GREEN_OUTPUT],
            [3, RED_CONNECTOR, 5, RED_CONNECTOR],
            [4, GREEN_CONNECTOR, 5, GREEN_CONNECTOR],
        ],
    }


@dataclass(frozen=True, slots=True)
class TransportBeltPulseReaderDevice:
    """Read item entries onto one belt segment as aligned Event payload/valid pulses."""

    label: str = "Transport belt Event pulse reader"

    def build(self) -> ExternalDeviceBlueprint:
        blueprint = _build_pulse_reader_blueprint(kind="transport-belt", label=self.label)
        return ExternalDeviceBlueprint(
            BELT_PULSE_READER_PROTOCOL,
            blueprint,
            (
                BoundDevicePort(
                    BELT_PULSE_READER_PROTOCOL.port("payload"),
                    DeviceEndpoint(1, RED_CONNECTOR, WireColor.RED, (0.5, 0.5)),
                ),
                BoundDevicePort(
                    BELT_PULSE_READER_PROTOCOL.port("valid"),
                    DeviceEndpoint(2, GREEN_CONNECTOR, WireColor.GREEN, (0.5, 2.5)),
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class InserterPulseReaderDevice:
    """Read each inserter pickup as aligned Event payload/valid pulses."""

    label: str = "Inserter Event pulse reader"

    def build(self) -> ExternalDeviceBlueprint:
        blueprint = _build_pulse_reader_blueprint(kind="inserter", label=self.label)
        return ExternalDeviceBlueprint(
            INSERTER_PULSE_READER_PROTOCOL,
            blueprint,
            (
                BoundDevicePort(
                    INSERTER_PULSE_READER_PROTOCOL.port("payload"),
                    DeviceEndpoint(1, RED_CONNECTOR, WireColor.RED, (0.5, 0.5)),
                ),
                BoundDevicePort(
                    INSERTER_PULSE_READER_PROTOCOL.port("valid"),
                    DeviceEndpoint(2, GREEN_CONNECTOR, WireColor.GREEN, (0.5, 2.5)),
                ),
            ),
        )


def build_transport_belt_pulse_reader_blueprint(
    *, label: str = "Transport belt Event pulse reader"
) -> Blueprint:
    return _build_pulse_reader_blueprint(kind="transport-belt", label=label)


def build_inserter_pulse_reader_blueprint(
    *, label: str = "Inserter Event pulse reader"
) -> Blueprint:
    return _build_pulse_reader_blueprint(kind="inserter", label=label)


def generate_transport_belt_pulse_reader_blueprint_string(
    *, label: str = "Transport belt Event pulse reader"
) -> str:
    return encode_blueprint(build_transport_belt_pulse_reader_blueprint(label=label))


def generate_inserter_pulse_reader_blueprint_string(
    *, label: str = "Inserter Event pulse reader"
) -> str:
    return encode_blueprint(build_inserter_pulse_reader_blueprint(label=label))


__all__ = [
    "BELT_PULSE_READER_PROTOCOL",
    "EVENT_VALID_SIGNAL",
    "INSERTER_PULSE_READER_PROTOCOL",
    "PULSE_READ_MODE",
    "InserterPulseReaderDevice",
    "TransportBeltPulseReaderDevice",
    "build_inserter_pulse_reader_blueprint",
    "build_transport_belt_pulse_reader_blueprint",
    "generate_inserter_pulse_reader_blueprint_string",
    "generate_transport_belt_pulse_reader_blueprint_string",
]
