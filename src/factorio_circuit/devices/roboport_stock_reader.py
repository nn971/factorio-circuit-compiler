"""Reusable roboport logistic-network stock reader.

The device exposes the roboport's logistic-network item contents as one stable RED Level vector.
Robot statistics are intentionally disabled here: they are scalar metadata and should become
separate typed ports rather than sharing the open item vector.
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
from factorio_circuit.ir.physical import WireColor
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality

FACTORIO_BLUEPRINT_VERSION: Final = 562954249306113
RED_CONNECTOR: Final = 1
ROBOPORT_READ_LOGISTICS: Final = 1

ROBOPORT_STOCK_READER_PROTOCOL: Final = DeviceProtocol(
    "roboport-stock-reader-v1",
    (
        DevicePortSpec(
            "stock",
            DevicePortDirection.OUTPUT,
            PayloadShape.VECTOR,
            TemporalModality.LEVEL,
            WireColor.RED,
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class RoboportStockReaderDevice:
    """Build a roboport whose logistic-network contents leave through a typed RED dock."""

    label: str = "Roboport logistic stock reader"

    def __post_init__(self) -> None:
        if not self.label:
            raise ValueError("roboport stock reader label must be non-empty")

    def build(self) -> ExternalDeviceBlueprint:
        blueprint = build_roboport_stock_reader_blueprint(label=self.label)
        return ExternalDeviceBlueprint(
            ROBOPORT_STOCK_READER_PROTOCOL,
            blueprint,
            (
                BoundDevicePort(
                    ROBOPORT_STOCK_READER_PROTOCOL.port("stock"),
                    DeviceEndpoint(1, RED_CONNECTOR, WireColor.RED, (4.5, 2.5)),
                ),
            ),
        )


def build_roboport_stock_reader_blueprint(
    *, label: str = "Roboport logistic stock reader"
) -> Blueprint:
    """Build the 4x4 roboport plus one flush output dock."""

    config = RoboportStockReaderDevice(label=label)
    return {
        "item": "blueprint",
        "label": config.label,
        "version": FACTORIO_BLUEPRINT_VERSION,
        "icons": [{"signal": {"name": "roboport"}, "index": 1}],
        "entities": [
            {
                "entity_number": 1,
                "name": "constant-combinator",
                "position": {"x": 4.5, "y": 2.5},
                "player_description": "ROBOPORT PORT stock — OUTPUT Level vector; RED",
            },
            {
                "entity_number": 2,
                "name": "roboport",
                "position": {"x": 2.0, "y": 2.0},
                "player_description": "ROBOPORT logistic-network stock reader",
                "control_behavior": {
                    "output_networks": {"red": True, "green": False},
                    "read_items_mode": ROBOPORT_READ_LOGISTICS,
                    "read_robot_stats": False,
                },
            },
        ],
        "wires": [[1, RED_CONNECTOR, 2, RED_CONNECTOR]],
    }


def generate_roboport_stock_reader_blueprint_string(
    *, label: str = "Roboport logistic stock reader"
) -> str:
    """Return an importable Factorio blueprint string for the stock reader."""

    return encode_blueprint(build_roboport_stock_reader_blueprint(label=label))


def main() -> None:
    print(generate_roboport_stock_reader_blueprint_string())


__all__ = [
    "ROBOPORT_READ_LOGISTICS",
    "ROBOPORT_STOCK_READER_PROTOCOL",
    "RoboportStockReaderDevice",
    "build_roboport_stock_reader_blueprint",
    "generate_roboport_stock_reader_blueprint_string",
]


if __name__ == "__main__":
    main()
