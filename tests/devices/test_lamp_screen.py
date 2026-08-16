from __future__ import annotations

import pytest

from factorio_circuit.devices._blueprint import decode_blueprint
from factorio_circuit.devices.lamp_screen import (
    DISPLAY_VIRTUAL_SIGNAL_POOL,
    PACKED_RGB_COLOR_MODE,
    PIXEL_COUNT,
    PIXEL_SIGNALS,
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    build_lamp_screen_blueprint,
    generate_lamp_screen_blueprint_string,
    pixel_index,
    pixel_signal,
    rgb,
)
from factorio_circuit.target.factorio.signals import DEFAULT_VIRTUAL_SIGNAL_POOL


def test_pixel_catalogue_is_fixed_row_major_and_unique() -> None:
    assert SCREEN_WIDTH == 16
    assert SCREEN_HEIGHT == 16
    assert PIXEL_COUNT == 256
    assert len(PIXEL_SIGNALS) == 256
    assert len(set(PIXEL_SIGNALS)) == 256

    assert pixel_index(0, 0) == 0
    assert pixel_index(15, 0) == 15
    assert pixel_index(0, 1) == 16
    assert pixel_index(15, 15) == 255
    assert pixel_signal(0, 0) == PIXEL_SIGNALS[0]
    assert pixel_signal(15, 15) == PIXEL_SIGNALS[-1]


def test_pixel_catalogue_prefers_display_only_virtual_signals() -> None:
    # The screen must not reserve the compiler's temporary allocation lanes: a compiled framebuffer
    # needs those virtual identities for its own intermediate arithmetic and predicates.
    assert len(DEFAULT_VIRTUAL_SIGNAL_POOL) == 51
    assert len(DISPLAY_VIRTUAL_SIGNAL_POOL) == 81
    assert not set(DISPLAY_VIRTUAL_SIGNAL_POOL) & set(DEFAULT_VIRTUAL_SIGNAL_POOL)
    assert PIXEL_SIGNALS[: len(DISPLAY_VIRTUAL_SIGNAL_POOL)] == DISPLAY_VIRTUAL_SIGNAL_POOL
    assert not set(PIXEL_SIGNALS) & set(DEFAULT_VIRTUAL_SIGNAL_POOL)
    assert all(signal.name != "signal-signal" for signal in PIXEL_SIGNALS)

    # The fixed ABI falls back to ordinary prototype signal kinds only after exhausting the real,
    # screen-local virtual catalogue.
    assert PIXEL_SIGNALS[80].kind == "virtual"
    assert PIXEL_SIGNALS[80].name == "signal-ghost"
    assert PIXEL_SIGNALS[81].kind == "item"
    assert PIXEL_SIGNALS[81].name == "wooden-chest"


@pytest.mark.parametrize("x,y", [(-1, 0), (0, -1), (16, 0), (0, 16)])
def test_pixel_coordinate_validation(x: int, y: int) -> None:
    with pytest.raises(ValueError, match="outside 16x16 screen"):
        pixel_signal(x, y)


def test_rgb_pack_and_validation() -> None:
    assert rgb(0, 0, 0) == 0x000000
    assert rgb(255, 0, 0) == 0xFF0000
    assert rgb(0, 255, 0) == 0x00FF00
    assert rgb(0, 0, 255) == 0x0000FF
    assert rgb(0x12, 0x34, 0x56) == 0x123456

    with pytest.raises(ValueError, match="red channel"):
        rgb(256, 0, 0)
    with pytest.raises(ValueError, match="green channel"):
        rgb(0, -1, 0)


def test_screen_blueprint_has_one_terminal_and_256_rgb_lamps() -> None:
    blueprint = build_lamp_screen_blueprint()
    entities = blueprint["entities"]

    assert len(entities) == 257
    assert entities[0]["name"] == "constant-combinator"
    lamps = entities[1:]
    assert all(lamp["name"] == "small-lamp" for lamp in lamps)

    for index, lamp in enumerate(lamps):
        control = lamp["control_behavior"]
        assert lamp["always_on"] is True
        assert control["use_colors"] is True
        assert control["color_mode"] == PACKED_RGB_COLOR_MODE
        assert control["rgb_signal"] == {
            "type": PIXEL_SIGNALS[index].kind,
            "name": PIXEL_SIGNALS[index].name,
        }


def test_screen_wiring_is_one_connected_green_bus() -> None:
    blueprint = build_lamp_screen_blueprint()
    wires = blueprint["wires"]

    assert len(wires) == 256
    assert all(
        source_connector == 2 and target_connector == 2
        for _, source_connector, _, target_connector in wires
    )

    adjacency: dict[int, set[int]] = {entity: set() for entity in range(1, 258)}
    for source, _source_connector, target, _target_connector in wires:
        adjacency[source].add(target)
        adjacency[target].add(source)

    seen = {1}
    stack = [1]
    while stack:
        current = stack.pop()
        for neighbor in adjacency[current] - seen:
            seen.add(neighbor)
            stack.append(neighbor)
    assert seen == set(range(1, 258))


def test_screen_blueprint_round_trip() -> None:
    blueprint = build_lamp_screen_blueprint()
    encoded = generate_lamp_screen_blueprint_string()
    assert decode_blueprint(encoded) == blueprint
