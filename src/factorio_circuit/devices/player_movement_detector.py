"""Generate the fixed eight-direction player movement detector blueprint.

The geometry is the user's tested gate/solar-panel prototype. Eight mutually exclusive wall/gate
proximity sensors are encoded onto fixed virtual-signal lanes and joined onto one green-wire bus.
The eight lamps from the tested blueprint are retained as direction indicators and share that bus.
"""

from __future__ import annotations

from typing import Final, Literal

from factorio_circuit.devices._blueprint import Blueprint, encode_blueprint

type Direction = Literal["N", "NE", "E", "SE", "S", "SW", "W", "NW"]

FACTORIO_BLUEPRINT_VERSION: Final = 562954249306113
GREEN_CONNECTOR: Final = 2

DIRECTION_ORDER: Final[tuple[Direction, ...]] = (
    "N",
    "NE",
    "E",
    "SE",
    "S",
    "SW",
    "W",
    "NW",
)
DIRECTION_SIGNALS: Final[dict[Direction, str]] = {
    "N": "signal-0",
    "NE": "signal-1",
    "E": "signal-2",
    "SE": "signal-3",
    "S": "signal-4",
    "SW": "signal-5",
    "W": "signal-6",
    "NW": "signal-7",
}

# Coordinates are the tested blueprint translated by (+89, +98), an integer offset which
# preserves Factorio tile alignment and every relative position.
SENSOR_POSITIONS: Final[dict[Direction, tuple[float, float]]] = {
    "N": (1.5, -3.5),
    "NE": (4.5, -3.5),
    "E": (4.5, 1.5),
    "SE": (4.5, 4.5),
    "S": (-0.5, 4.5),
    "SW": (-3.5, 4.5),
    "W": (-3.5, -0.5),
    "NW": (-3.5, -3.5),
}
INDICATOR_POSITIONS: Final[dict[Direction, tuple[float, float]]] = {
    "N": (0.5, -5.5),
    "NE": (6.5, -5.5),
    "E": (6.5, 0.5),
    "SE": (6.5, 6.5),
    "S": (0.5, 6.5),
    "SW": (-5.5, 6.5),
    "W": (-5.5, 0.5),
    "NW": (-5.5, -5.5),
}
_DIRECTION_BY_SENSOR_POSITION: Final = {
    position: direction for direction, position in SENSOR_POSITIONS.items()
}
_DIRECTION_BY_INDICATOR_POSITION: Final = {
    position: direction for direction, position in INDICATOR_POSITIONS.items()
}

# (prototype name, x, y, optional Factorio direction)
_LAYOUT: Final[tuple[tuple[str, float, float, int | None], ...]] = (
    ("small-lamp", -5.5, -5.5, None),
    ("small-lamp", -5.5, 0.5, None),
    ("small-lamp", -5.5, 6.5, None),
    ("stone-wall", -3.5, -3.5, None),
    ("stone-wall", -3.5, -2.5, None),
    ("stone-wall", -3.5, -1.5, None),
    ("stone-wall", -3.5, -0.5, None),
    ("gate", -3.5, 0.5, None),
    ("stone-wall", -3.5, 1.5, None),
    ("stone-wall", -3.5, 2.5, None),
    ("gate", -3.5, 3.5, None),
    ("stone-wall", -3.5, 4.5, None),
    ("gate", -2.5, -3.5, 4),
    ("stone-wall", -2.5, 4.5, None),
    ("stone-wall", -1.5, -3.5, None),
    ("solar-panel", -1.5, 1.5, None),
    ("stone-wall", -1.5, 4.5, None),
    ("stone-wall", -0.5, -3.5, None),
    ("solar-panel", -0.5, -1.5, None),
    ("stone-wall", -0.5, 4.5, None),
    ("small-lamp", 0.5, -5.5, None),
    ("gate", 0.5, -3.5, 4),
    ("gate", 0.5, 4.5, 4),
    ("small-lamp", 0.5, 6.5, None),
    ("stone-wall", 1.5, -3.5, None),
    ("solar-panel", 1.5, 2.5, None),
    ("stone-wall", 1.5, 4.5, None),
    ("stone-wall", 2.5, -3.5, None),
    ("solar-panel", 2.5, -0.5, None),
    ("stone-wall", 2.5, 4.5, None),
    ("stone-wall", 3.5, -3.5, None),
    ("gate", 3.5, 4.5, 4),
    ("stone-wall", 4.5, -3.5, None),
    ("gate", 4.5, -2.5, None),
    ("stone-wall", 4.5, -1.5, None),
    ("stone-wall", 4.5, -0.5, None),
    ("gate", 4.5, 0.5, None),
    ("stone-wall", 4.5, 1.5, None),
    ("stone-wall", 4.5, 2.5, None),
    ("stone-wall", 4.5, 3.5, None),
    ("stone-wall", 4.5, 4.5, None),
    ("small-lamp", 6.5, -5.5, None),
    ("small-lamp", 6.5, 0.5, None),
    ("small-lamp", 6.5, 6.5, None),
)


def _virtual_signal(name: str) -> dict[str, str]:
    return {"type": "virtual", "name": name}


def build_player_movement_detector_blueprint() -> Blueprint:
    """Build the fixed-layout eight-way movement detector as Factorio blueprint JSON."""
    entities: list[dict[str, object]] = []
    sensor_entities: dict[Direction, int] = {}
    indicator_entities: dict[Direction, int] = {}

    for entity_number, (name, x, y, direction) in enumerate(_LAYOUT, start=1):
        entity: dict[str, object] = {
            "entity_number": entity_number,
            "name": name,
            "position": {"x": x, "y": y},
        }
        if direction is not None:
            entity["direction"] = direction

        sensor_direction = _DIRECTION_BY_SENSOR_POSITION.get((x, y))
        if sensor_direction is not None:
            entity["control_behavior"] = {
                # Preserve the tested prototype's gate-control mode.
                "circuit_open_gate": True,
                "circuit_read_sensor": True,
                "output_signal": _virtual_signal(DIRECTION_SIGNALS[sensor_direction]),
            }
            sensor_entities[sensor_direction] = entity_number

        indicator_direction = _DIRECTION_BY_INDICATOR_POSITION.get((x, y))
        if indicator_direction is not None:
            lane = DIRECTION_SIGNALS[indicator_direction]
            entity["control_behavior"] = {
                "circuit_enabled": True,
                "circuit_condition": {
                    "first_signal": _virtual_signal(lane),
                    "constant": 0,
                    "comparator": ">",
                },
            }
            entity["player_description"] = f"{indicator_direction} indicator / movement bus"
            indicator_entities[indicator_direction] = entity_number

        entities.append(entity)

    # Ring the sensors together, then attach every retained indicator lamp to its own sensor.
    # All sixteen connections therefore belong to one green circuit network.
    wires: list[list[int]] = []
    for index, direction in enumerate(DIRECTION_ORDER):
        next_direction = DIRECTION_ORDER[(index + 1) % len(DIRECTION_ORDER)]
        wires.append(
            [
                sensor_entities[direction],
                GREEN_CONNECTOR,
                sensor_entities[next_direction],
                GREEN_CONNECTOR,
            ]
        )
        wires.append(
            [
                sensor_entities[direction],
                GREEN_CONNECTOR,
                indicator_entities[direction],
                GREEN_CONNECTOR,
            ]
        )

    return {
        "item": "blueprint",
        "label": "Player movement detector (8-way one-hot)",
        "version": FACTORIO_BLUEPRINT_VERSION,
        "icons": [{"signal": {"name": "gate"}, "index": 1}],
        "entities": entities,
        "wires": wires,
    }


def generate_player_movement_detector_blueprint_string() -> str:
    """Return an importable Factorio blueprint string."""
    return encode_blueprint(build_player_movement_detector_blueprint())


def main() -> None:
    print(generate_player_movement_detector_blueprint_string())


if __name__ == "__main__":
    main()
