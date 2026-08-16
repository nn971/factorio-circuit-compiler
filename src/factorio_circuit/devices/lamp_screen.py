"""Generate a fixed 16x16 packed-RGB lamp screen blueprint."""

from __future__ import annotations

from typing import Final

from factorio_circuit.devices._blueprint import Blueprint, encode_blueprint
from factorio_circuit.ir.physical import SignalId
from factorio_circuit.target.factorio.signals import DEFAULT_VIRTUAL_SIGNAL_POOL

SCREEN_WIDTH: Final = 16
SCREEN_HEIGHT: Final = 16
PIXEL_COUNT: Final = SCREEN_WIDTH * SCREEN_HEIGHT
RED_CONNECTOR: Final = 1
GREEN_CONNECTOR: Final = 2
BUS_CONNECTORS: Final = (RED_CONNECTOR, GREEN_CONNECTOR)
PACKED_RGB_COLOR_MODE: Final = 2
FACTORIO_BLUEPRINT_VERSION: Final = 562954249306113

# Real base-game virtual signals deliberately disjoint from the compiler's small internal allocator
# pool. Selector pseudo-signals (signal-each/everything/anything) are excluded because they are
# circuit-language operands, not independent framebuffer channels.
_DISPLAY_VIRTUAL_SIGNAL_NAMES: Final[tuple[str, ...]] = (
    "signal-no-entry",
    "signal-heart",
    "signal-alert",
    "signal-comma",
    "signal-letter-dot",
    "signal-exclamation-mark",
    "signal-question-mark",
    "signal-colon",
    "signal-slash",
    "signal-apostrophe",
    "signal-quotation-mark",
    "signal-ampersand",
    "signal-circumflex-accent",
    "signal-number-sign",
    "signal-percent",
    "shape-vertical",
    "shape-horizontal",
    "shape-diagonal",
    "shape-diagonal-2",
    "shape-curve",
    "shape-curve-2",
    "shape-curve-3",
    "shape-curve-4",
    "shape-cross",
    "shape-diagonal-cross",
    "shape-corner",
    "shape-corner-2",
    "shape-corner-3",
    "shape-corner-4",
    "shape-t",
    "shape-t-2",
    "shape-t-3",
    "shape-t-4",
    "shape-circle",
    "up-arrow",
    "up-right-arrow",
    "right-arrow",
    "down-right-arrow",
    "down-arrow",
    "down-left-arrow",
    "left-arrow",
    "up-left-arrow",
    "signal-rightwards-leftwards-arrow",
    "signal-upwards-downwards-arrow",
    "signal-shuffle",
    "signal-left-right-arrow",
    "signal-up-down-arrow",
    "signal-clockwise-circle-arrow",
    "signal-anticlockwise-circle-arrow",
    "signal-input",
    "signal-output",
    "signal-fuel",
    "signal-lightning",
    "signal-battery-full",
    "signal-battery-mid-level",
    "signal-battery-low",
    "signal-radioactivity",
    "signal-thermometer-blue",
    "signal-thermometer-red",
    "signal-fire",
    "signal-explosion",
    "signal-snowflake",
    "signal-liquid",
    "signal-stack-size",
    "signal-recycle",
    "signal-trash-bin",
    "signal-science-pack",
    "signal-map-marker",
    "signal-white-flag",
    "signal-lock",
    "signal-unlock",
    "signal-mining",
    "signal-hourglass",
    "signal-alarm",
    "signal-sun",
    "signal-moon",
    "signal-speed",
    "signal-skull",
    "signal-damage",
    "signal-weapon",
    "signal-ghost",
)

DISPLAY_VIRTUAL_SIGNAL_POOL: Final[tuple[SignalId, ...]] = tuple(
    SignalId("virtual", name) for name in _DISPLAY_VIRTUAL_SIGNAL_NAMES
)
if len(DISPLAY_VIRTUAL_SIGNAL_POOL) != 81:  # pragma: no cover - import-time invariant
    raise RuntimeError(
        f"display virtual signal catalogue contains {len(DISPLAY_VIRTUAL_SIGNAL_POOL)} lanes; "
        "expected 81"
    )
if len(set(DISPLAY_VIRTUAL_SIGNAL_POOL)) != len(DISPLAY_VIRTUAL_SIGNAL_POOL):  # pragma: no cover
    raise RuntimeError("display virtual signal catalogue contains duplicate lanes")
if set(DISPLAY_VIRTUAL_SIGNAL_POOL) & set(DEFAULT_VIRTUAL_SIGNAL_POOL):  # pragma: no cover
    raise RuntimeError("display virtual lanes must not consume compiler allocation signals")

# These base-game prototypes all have item, recipe, and entity signal identities with the same name.
# They are fallback framebuffer lanes after the display-only virtual catalogue is exhausted.
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

_FALLBACK_PIXEL_SIGNALS: Final[tuple[SignalId, ...]] = tuple(
    SignalId(kind, name)
    for name in _PLACEABLE_SIGNAL_NAMES
    for kind in ("item", "recipe", "entity")
)
_PIXEL_SIGNAL_CANDIDATES: Final[tuple[SignalId, ...]] = (
    DISPLAY_VIRTUAL_SIGNAL_POOL + _FALLBACK_PIXEL_SIGNALS
)
if len(_PIXEL_SIGNAL_CANDIDATES) < PIXEL_COUNT:  # pragma: no cover - import-time invariant
    raise RuntimeError(
        f"pixel signal catalogue contains only {len(_PIXEL_SIGNAL_CANDIDATES)} candidates; "
        f"expected at least {PIXEL_COUNT}"
    )

# Stable row-major screen ABI. Pixel lanes never overlap the compiler's temporary virtual allocator,
# so a compiled program can drive the entire framebuffer without exhausting internal signal colors.
PIXEL_SIGNALS: Final[tuple[SignalId, ...]] = _PIXEL_SIGNAL_CANDIDATES[:PIXEL_COUNT]
if len(set(PIXEL_SIGNALS)) != PIXEL_COUNT:  # pragma: no cover - import-time invariant
    raise RuntimeError("pixel signal catalogue contains duplicate lanes")
if set(PIXEL_SIGNALS) & set(DEFAULT_VIRTUAL_SIGNAL_POOL):  # pragma: no cover
    raise RuntimeError("framebuffer lanes overlap compiler allocation signals")


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

    Pixel (0, 0) is the top-left lamp. Every lamp participates in parallel red and green circuit
    buses. Connect only the compiler output's assigned color at the labelled terminal.
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

    # Two identical short-hop serpentine networks make the passive display input independent of the
    # wire color chosen by compiler synthesis. Only one color should be connected by the consumer.
    path: list[tuple[int, int]] = []
    for y in range(SCREEN_HEIGHT):
        xs = range(SCREEN_WIDTH) if y % 2 == 0 else range(SCREEN_WIDTH - 1, -1, -1)
        path.extend((x, y) for x in xs)

    wires: list[list[int]] = []
    for connector in BUS_CONNECTORS:
        wires.append([1, connector, _lamp_number(0, 0), connector])
        for (left_x, left_y), (right_x, right_y) in zip(path[:-1], path[1:], strict=True):
            wires.append(
                [
                    _lamp_number(left_x, left_y),
                    connector,
                    _lamp_number(right_x, right_y),
                    connector,
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
