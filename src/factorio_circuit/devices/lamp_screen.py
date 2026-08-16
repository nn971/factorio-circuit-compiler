"""Generate a fixed 16x16 packed-RGB lamp screen blueprint."""

from __future__ import annotations

from typing import Final

from factorio_circuit.devices._blueprint import Blueprint, encode_blueprint
from factorio_circuit.ir.physical import SignalId
from factorio_circuit.target.factorio.signals import DEFAULT_VIRTUAL_SIGNAL_POOL

SCREEN_WIDTH: Final = 16
SCREEN_HEIGHT: Final = 16
PIXEL_COUNT: Final = SCREEN_WIDTH * SCREEN_HEIGHT
GREEN_CONNECTOR: Final = 2
PACKED_RGB_COLOR_MODE: Final = 2
FACTORIO_BLUEPRINT_VERSION: Final = 562954249306113

# These base-game prototypes all have item, recipe, and entity signal identities with the same name.
# Reusing the three signal kinds gives a compact, deterministic catalogue without depending on
# Space Age content or signal quality.
_PLACEABLE_SIGNAL_NAMES: Final[tuple[str, ...]] = (
    "wooden-chest",
    "iron-chest",
    "steel-chest",
    "storage-tank",
    "transport-belt",
    "fast-transport-belt",
    "express-transport-belt",
    "underground-belt",
    "fast-underground-belt",
    "express-underground-belt",
    "splitter",
    "fast-splitter",
    "express-splitter",
    "burner-inserter",
    "inserter",
    "long-handed-inserter",
    "fast-inserter",
    "bulk-inserter",
    "small-electric-pole",
    "medium-electric-pole",
    "big-electric-pole",
    "substation",
    "pipe",
    "pipe-to-ground",
    "pump",
    "stone-wall",
    "gate",
    "train-stop",
    "rail-signal",
    "rail-chain-signal",
    "locomotive",
    "cargo-wagon",
    "fluid-wagon",
    "artillery-wagon",
    "car",
    "tank",
    "spidertron",
    "logistic-robot",
    "construction-robot",
    "assembling-machine-1",
    "assembling-machine-2",
    "assembling-machine-3",
    "oil-refinery",
    "chemical-plant",
    "centrifuge",
    "electric-furnace",
    "steel-furnace",
    "stone-furnace",
    "lab",
    "beacon",
    "rocket-silo",
    "radar",
    "roboport",
    "accumulator",
    "solar-panel",
    "steam-engine",
    "steam-turbine",
    "boiler",
    "nuclear-reactor",
    "heat-pipe",
    "heat-exchanger",
    "offshore-pump",
    "pumpjack",
    "electric-mining-drill",
)

_BASIC_ITEM_SIGNAL_NAMES: Final[tuple[str, ...]] = (
    "stone-brick",
    "wood",
    "coal",
    "stone",
    "iron-ore",
    "copper-ore",
    "iron-plate",
    "copper-plate",
    "copper-cable",
    "iron-stick",
    "iron-gear-wheel",
    "electronic-circuit",
    "advanced-circuit",
)

PIXEL_SIGNALS: Final[tuple[SignalId, ...]] = (
    DEFAULT_VIRTUAL_SIGNAL_POOL
    + tuple(
        SignalId(kind, name)
        for name in _PLACEABLE_SIGNAL_NAMES
        for kind in ("item", "recipe", "entity")
    )
    + tuple(SignalId("item", name) for name in _BASIC_ITEM_SIGNAL_NAMES)
)

if len(PIXEL_SIGNALS) != PIXEL_COUNT:  # pragma: no cover - import-time invariant
    raise RuntimeError(
        f"pixel signal catalogue contains {len(PIXEL_SIGNALS)} lanes; expected {PIXEL_COUNT}"
    )
if len(set(PIXEL_SIGNALS)) != PIXEL_COUNT:  # pragma: no cover - import-time invariant
    raise RuntimeError("pixel signal catalogue contains duplicate lanes")


def pixel_index(x: int, y: int) -> int:
    """Return the row-major framebuffer lane index for one screen coordinate."""
    if not 0 <= x < SCREEN_WIDTH or not 0 <= y < SCREEN_HEIGHT:
        raise ValueError(
            f"pixel coordinate ({x}, {y}) is outside {SCREEN_WIDTH}x{SCREEN_HEIGHT} screen"
        )
    return y * SCREEN_WIDTH + x


def pixel_signal(x: int, y: int) -> SignalId:
    """Return the fixed Factorio signal lane assigned to one pixel."""
    return PIXEL_SIGNALS[pixel_index(x, y)]


def rgb(red: int, green: int, blue: int) -> int:
    """Pack three 8-bit channels into Factorio's 0xRRGGBB lamp value."""
    for name, value in (("red", red), ("green", green), ("blue", blue)):
        if not 0 <= value <= 255:
            raise ValueError(f"{name} channel {value} is outside [0, 255]")
    return (red << 16) | (green << 8) | blue


def _signal_json(signal: SignalId) -> dict[str, str]:
    return {"type": signal.kind, "name": signal.name}


def _lamp_entity(entity_number: int, x: int, y: int) -> dict[str, object]:
    lane = pixel_signal(x, y)
    return {
        "entity_number": entity_number,
        "name": "small-lamp",
        "position": {"x": x + 0.5, "y": y + 0.5},
        "always_on": True,
        "control_behavior": {
            "use_colors": True,
            "color_mode": PACKED_RGB_COLOR_MODE,
            "rgb_signal": _signal_json(lane),
        },
    }


def _lamp_number(x: int, y: int) -> int:
    return 2 + pixel_index(x, y)


def build_lamp_screen_blueprint() -> Blueprint:
    """Build the fixed 16x16 screen as Factorio blueprint JSON.

    Pixel (0, 0) is the top-left lamp. All lamps share one green circuit network. An empty
    constant combinator immediately to the left of the top row is the labelled framebuffer input.
    """
    entities: list[dict[str, object]] = [
        {
            "entity_number": 1,
            "name": "constant-combinator",
            "position": {"x": -1.5, "y": 0.5},
            "player_description": "DISPLAY INPUT: 16x16 packed-RGB framebuffer",
        }
    ]
    entities.extend(
        _lamp_entity(_lamp_number(x, y), x, y)
        for y in range(SCREEN_HEIGHT)
        for x in range(SCREEN_WIDTH)
    )

    wires: list[list[int]] = [[1, GREEN_CONNECTOR, _lamp_number(0, 0), GREEN_CONNECTOR]]

    # One short serpentine chain keeps every wire between adjacent lamps while making all pixels
    # members of the same circuit network.
    path: list[tuple[int, int]] = []
    for y in range(SCREEN_HEIGHT):
        xs = range(SCREEN_WIDTH) if y % 2 == 0 else range(SCREEN_WIDTH - 1, -1, -1)
        path.extend((x, y) for x in xs)
    for (left_x, left_y), (right_x, right_y) in zip(path, path[1:], strict=True):
        wires.append(
            [
                _lamp_number(left_x, left_y),
                GREEN_CONNECTOR,
                _lamp_number(right_x, right_y),
                GREEN_CONNECTOR,
            ]
        )

    return {
        "item": "blueprint",
        "label": "16x16 packed-RGB lamp screen",
        "version": FACTORIO_BLUEPRINT_VERSION,
        "icons": [{"signal": {"name": "small-lamp"}, "index": 1}],
        "entities": entities,
        "wires": wires,
    }


def generate_lamp_screen_blueprint_string() -> str:
    """Return an importable Factorio blueprint string for the screen."""
    return encode_blueprint(build_lamp_screen_blueprint())


def main() -> None:
    print(generate_lamp_screen_blueprint_string())


if __name__ == "__main__":
    main()
