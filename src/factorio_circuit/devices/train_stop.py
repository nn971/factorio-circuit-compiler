"""Reusable train-stop command/status interface for Milestone F4.

The device gives a vanilla Factorio train stop two electrically separated Level-vector buses:

* GREEN ``commands`` feeds train-stop inputs and signals sent to a stopped train;
* RED ``status`` carries stopped-train contents plus train-stop metadata.

The stop reserves Factorio's canonical train-stop virtual signals on those buses:
``signal-L`` for circuit train limit, ``signal-P`` for priority, ``signal-T`` for the stopped-train
id, and ``signal-C`` for incoming train count.  Keeping command and status networks on different
wire colors avoids the train stop reading its own outputs as commands.
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

TRAIN_STOPPED_SIGNAL: Final = SignalId("virtual", "signal-T")
TRAINS_COUNT_SIGNAL: Final = SignalId("virtual", "signal-C")
TRAINS_LIMIT_SIGNAL: Final = SignalId("virtual", "signal-L")
TRAIN_PRIORITY_SIGNAL: Final = SignalId("virtual", "signal-P")

TRAIN_STOP_PROTOCOL: Final = DeviceProtocol(
    "train-stop-interface-v1",
    (
        DevicePortSpec(
            "commands",
            DevicePortDirection.INPUT,
            PayloadShape.VECTOR,
            TemporalModality.LEVEL,
            WireColor.GREEN,
        ),
        DevicePortSpec(
            "status",
            DevicePortDirection.OUTPUT,
            PayloadShape.VECTOR,
            TemporalModality.LEVEL,
            WireColor.RED,
        ),
    ),
)


def _signal_json(signal: SignalId) -> dict[str, str]:
    return {"type": signal.kind, "name": signal.name}


def _networks(*, red: bool, green: bool) -> dict[str, bool]:
    return {"red": red, "green": green}


def build_train_stop_blueprint(
    *,
    label: str = "Train stop circuit interface",
    station: str = "Circuit interface",
    direction: int = 0,
) -> Blueprint:
    """Build one train stop with separate GREEN command and RED status docks."""

    if not label:
        raise ValueError("train stop interface label must be non-empty")
    if not station:
        raise ValueError("train stop station name must be non-empty")
    if direction not in {0, 4, 8, 12}:
        raise ValueError("train stop direction must be one of 0, 4, 8, 12")

    return {
        "item": "blueprint",
        "label": label,
        "version": FACTORIO_BLUEPRINT_VERSION,
        "icons": [{"signal": {"name": "train-stop"}, "index": 1}],
        "entities": [
            {
                "entity_number": 1,
                "name": "constant-combinator",
                "position": {"x": 0.5, "y": 0.5},
                "player_description": "TRAIN STOP PORT commands — INPUT Level vector; GREEN",
            },
            {
                "entity_number": 2,
                "name": "constant-combinator",
                "position": {"x": 0.5, "y": 2.5},
                "player_description": "TRAIN STOP PORT status — OUTPUT Level vector; RED",
            },
            {
                "entity_number": 3,
                "name": "train-stop",
                "position": {"x": 3.5, "y": 1.0},
                "direction": direction,
                "station": station,
                "player_description": "TRAIN STOP typed command/status interface",
                "control_behavior": {
                    "input_networks": _networks(red=False, green=True),
                    "output_networks": _networks(red=True, green=False),
                    "send_to_train": True,
                    "read_from_train": True,
                    "read_stopped_train": True,
                    "train_stopped_signal": _signal_json(TRAIN_STOPPED_SIGNAL),
                    "set_trains_limit": True,
                    "trains_limit_signal": _signal_json(TRAINS_LIMIT_SIGNAL),
                    "read_trains_count": True,
                    "trains_count_signal": _signal_json(TRAINS_COUNT_SIGNAL),
                    "set_priority": True,
                    "priority_signal": _signal_json(TRAIN_PRIORITY_SIGNAL),
                },
            },
        ],
        "wires": [
            [1, GREEN_CONNECTOR, 3, GREEN_CONNECTOR],
            [2, RED_CONNECTOR, 3, RED_CONNECTOR],
        ],
    }


@dataclass(frozen=True, slots=True)
class TrainStopDevice:
    """Typed vanilla train-stop interface with command and status vector buses."""

    label: str = "Train stop circuit interface"
    station: str = "Circuit interface"
    direction: int = 0

    def __post_init__(self) -> None:
        if not self.label:
            raise ValueError("train stop interface label must be non-empty")
        if not self.station:
            raise ValueError("train stop station name must be non-empty")
        if self.direction not in {0, 4, 8, 12}:
            raise ValueError("train stop direction must be one of 0, 4, 8, 12")

    def build(self) -> ExternalDeviceBlueprint:
        blueprint = build_train_stop_blueprint(
            label=self.label,
            station=self.station,
            direction=self.direction,
        )
        return ExternalDeviceBlueprint(
            TRAIN_STOP_PROTOCOL,
            blueprint,
            (
                BoundDevicePort(
                    TRAIN_STOP_PROTOCOL.port("commands"),
                    DeviceEndpoint(1, GREEN_CONNECTOR, WireColor.GREEN, (0.5, 0.5)),
                ),
                BoundDevicePort(
                    TRAIN_STOP_PROTOCOL.port("status"),
                    DeviceEndpoint(2, RED_CONNECTOR, WireColor.RED, (0.5, 2.5)),
                ),
            ),
        )


def generate_train_stop_blueprint_string(
    *,
    label: str = "Train stop circuit interface",
    station: str = "Circuit interface",
    direction: int = 0,
) -> str:
    return encode_blueprint(
        build_train_stop_blueprint(label=label, station=station, direction=direction)
    )


def main() -> None:
    print(generate_train_stop_blueprint_string())


if __name__ == "__main__":
    main()


__all__ = [
    "TRAIN_PRIORITY_SIGNAL",
    "TRAIN_STOPPED_SIGNAL",
    "TRAINS_COUNT_SIGNAL",
    "TRAINS_LIMIT_SIGNAL",
    "TRAIN_STOP_PROTOCOL",
    "TrainStopDevice",
    "build_train_stop_blueprint",
    "generate_train_stop_blueprint_string",
]
