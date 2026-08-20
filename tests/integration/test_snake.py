from __future__ import annotations

import pytest

from benchmarks.snake.model import (
    ARROW_SIGNALS,
    BODY_CAPACITY,
    BODY_COLOR,
    CELL_COUNT,
    DIR_E,
    DIR_N,
    FOOD_CELL_IDS,
    FOOD_COLOR,
    HEAD_COLOR,
    MAX_LENGTH,
    PIXEL_ID_ROM,
    _decode_cell_pixels,
    build_snake_circuit,
)
from factorio_circuit import Circuit
from factorio_circuit.devices import pixel_signal
from factorio_circuit.ir.semantic import is_vector_value
from factorio_circuit.simulate.semantic import LogicalOutput, simulate_stream


def _movement(**directions: int) -> dict[object, int]:
    return {ARROW_SIGNALS[direction]: value for direction, value in directions.items()}


def _simulate(
    movements: list[dict[object, int]],
    *,
    resets: list[int] | None = None,
    logical_steps_per_move: int = 1,
) -> list[dict[str, LogicalOutput]]:
    """Run the gameplay/state model without the heavyweight framebuffer history."""

    module = build_snake_circuit(
        logical_steps_per_move=logical_steps_per_move,
        render_framebuffer=False,
    ).build()
    reset_rows = [0] * len(movements) if resets is None else resets
    if len(reset_rows) != len(movements):
        raise ValueError("resets must have the same length as movements")
    trace = simulate_stream(
        module,
        [
            {"movement": movement, "reset": reset}
            for movement, reset in zip(movements, reset_rows, strict=True)
        ],
    )
    names = tuple(name for name in module.output.names if name is not None)
    assert len(names) == len(module.output.values)
    return [dict(zip(names, row, strict=True)) for row in trace]


@pytest.mark.slow
@pytest.mark.acceptance
def test_snake_waits_at_center_until_first_direction_gesture() -> None:
    rows = _simulate([{}, {}, {}])

    assert all(row["head_x"] == 8 and row["head_y"] == 8 for row in rows)
    assert all(row["started"] == 0 for row in rows)
    assert all(row["dead"] == 0 for row in rows)


@pytest.mark.slow
@pytest.mark.acceptance
def test_snake_first_gesture_can_choose_any_direction() -> None:
    rows = _simulate([_movement(W=1), {}])

    assert rows[0]["started"] == 1
    assert (rows[0]["head_x"], rows[0]["head_y"]) == (7, 8)
    assert rows[1]["head_x"] == 6


@pytest.mark.slow
@pytest.mark.acceptance
def test_snake_starts_east_eats_first_food_and_grows() -> None:
    rows = _simulate([_movement(E=1), {}, {}, {}])

    assert rows[0]["head_x"] == 9
    assert rows[0]["head_y"] == 8
    assert rows[0]["direction"] == DIR_E
    assert rows[0]["started"] == 1
    assert rows[0]["score"] == 0
    assert rows[0]["length"] == 1

    assert rows[1]["head_x"] == 10
    assert rows[1]["score"] == 0

    # The first deterministic food is at (11, 8), three moves east of the initial head.
    assert rows[2]["head_x"] == 11
    assert rows[2]["head_y"] == 8
    assert rows[2]["score"] == 1
    assert rows[2]["length"] == 2
    assert rows[2]["food_cell"] == FOOD_CELL_IDS[1]


@pytest.mark.slow
@pytest.mark.acceptance
def test_snake_rejects_reverse_and_uses_diagonal_as_a_turn() -> None:
    rows = _simulate(
        [
            _movement(E=1),
            _movement(N=1),
            _movement(S=1),
            _movement(NE=1),
        ]
    )

    # Start east, then turn north.
    assert (rows[0]["head_x"], rows[0]["head_y"]) == (9, 8)
    assert (rows[1]["head_x"], rows[1]["head_y"]) == (9, 7)
    assert rows[1]["direction"] == DIR_N

    # South is the exact reverse of north and is ignored.
    assert (rows[2]["head_x"], rows[2]["head_y"]) == (9, 6)
    assert rows[2]["direction"] == DIR_N

    # NE while travelling vertically requests its perpendicular east component.
    assert (rows[3]["head_x"], rows[3]["head_y"]) == (10, 6)
    assert rows[3]["direction"] == DIR_E


@pytest.mark.slow
@pytest.mark.acceptance
def test_snake_queues_a_short_direction_gesture_until_next_move() -> None:
    rows = _simulate(
        [_movement(E=1), _movement(N=1), {}, {}],
        logical_steps_per_move=3,
    )

    assert (rows[0]["head_x"], rows[0]["head_y"]) == (9, 8)
    assert rows[0]["direction"] == DIR_E
    assert (rows[1]["head_x"], rows[1]["head_y"]) == (9, 8)
    assert (rows[2]["head_x"], rows[2]["head_y"]) == (9, 8)

    # The detector is neutral again by the move boundary, but the queued request is retained.
    assert (rows[3]["head_x"], rows[3]["head_y"]) == (9, 7)
    assert rows[3]["direction"] == DIR_N


@pytest.mark.slow
@pytest.mark.acceptance
def test_snake_stops_on_wall_collision() -> None:
    rows = _simulate([_movement(E=1), *({} for _ in range(8))])

    # The east wall is after x=15. The failed move leaves the head at x=15 and latches dead=1.
    assert rows[6]["head_x"] == 15
    assert rows[6]["dead"] == 0
    assert rows[7]["head_x"] == 15
    assert rows[7]["dead"] == 1
    assert rows[8]["head_x"] == 15
    assert rows[8]["dead"] == 1


@pytest.mark.slow
@pytest.mark.acceptance
def test_snake_detects_self_collision_with_packed_ttl_body() -> None:
    def straight(direction: str, steps: int) -> list[dict[object, int]]:
        return [_movement(**{direction: 1}), *({} for _ in range(steps - 1))]

    # Visit the first four deterministic food cells, yielding a length-five snake. The final
    # south/east/north hook attempts to enter a non-tail body pixel; the head therefore stays put
    # and death latches. This exercises TTL aging, growth retention, tail expiry, and membership.
    movements = [
        *straight("E", 3),
        *straight("S", 5),
        *straight("W", 7),
        *straight("N", 12),
        *straight("E", 9),
        *straight("S", 5),
        *straight("W", 7),
        _movement(S=1),
        _movement(E=1),
        _movement(N=1),
    ]
    rows = _simulate(movements)

    assert (rows[47]["head_x"], rows[47]["head_y"]) == (6, 6)
    assert rows[47]["score"] == 4
    assert rows[47]["length"] == 5
    assert rows[49]["dead"] == 0
    assert (rows[49]["head_x"], rows[49]["head_y"]) == (7, 7)
    assert rows[50]["dead"] == 1
    assert (rows[50]["head_x"], rows[50]["head_y"]) == (7, 7)


@pytest.mark.slow
@pytest.mark.acceptance
def test_reset_wins_over_movement_restores_initial_state_and_rearms_game() -> None:
    movements = [
        _movement(E=1),
        {},
        {},
        {},
        {},
        {},
        {},
        {},
        _movement(W=1),
        _movement(W=1),
    ]
    resets = [0] * 8 + [1, 0]
    rows = _simulate(movements, resets=resets)

    assert rows[7]["dead"] == 1
    assert rows[7]["score"] == 1

    # Reset is asserted together with a west gesture. Reset wins atomically and restores every
    # externally visible game-state field to the startup state.
    reset_row = rows[8]
    assert (reset_row["head_x"], reset_row["head_y"]) == (8, 8)
    assert reset_row["direction"] == DIR_E
    assert reset_row["score"] == 0
    assert reset_row["length"] == 1
    assert reset_row["dead"] == 0
    assert reset_row["started"] == 0
    assert reset_row["food_cell"] == FOOD_CELL_IDS[0]

    # The simultaneous gesture did not survive reset; a fresh gesture starts the next game.
    assert (rows[9]["head_x"], rows[9]["head_y"]) == (7, 8)
    assert rows[9]["started"] == 1


def test_framebuffer_decoder_and_color_composition_are_cheaply_covered() -> None:
    """Exercise the renderer algebra without constructing/simulating the full Snake state graph."""

    circuit = Circuit("snake_framebuffer_primitives")
    head_cell = circuit.input("head_cell")
    body_cell = circuit.input("body_cell")
    food_cell = circuit.input("food_cell")
    pixel_id_rom = circuit.constant_signals(PIXEL_ID_ROM)
    framebuffer = (
        _decode_cell_pixels(pixel_id_rom, body_cell) * BODY_COLOR
        + _decode_cell_pixels(pixel_id_rom, head_cell) * HEAD_COLOR
        + _decode_cell_pixels(pixel_id_rom, food_cell) * FOOD_COLOR
    )
    circuit.output("framebuffer", framebuffer)

    rows = simulate_stream(
        circuit.build(),
        [
            {
                "head_cell": 8 * 16 + 8 + 1,
                "body_cell": 8 * 16 + 7 + 1,
                "food_cell": FOOD_CELL_IDS[0],
            }
        ],
    )
    frame = rows[0][0]

    assert isinstance(frame, dict)
    assert frame == {
        pixel_signal(7, 8): BODY_COLOR,
        pixel_signal(8, 8): HEAD_COLOR,
        pixel_signal(11, 8): FOOD_COLOR,
    }


@pytest.mark.slow
@pytest.mark.acceptance
def test_full_snake_build_contains_reset_framebuffer_and_packed_body_state() -> None:
    module = build_snake_circuit(render_framebuffer=True).build()

    assert [item.name for item in module.inputs] == ["reset"]
    assert [item.name for item in module.vector_inputs] == ["movement"]
    assert module.output.names[0] == "framebuffer"
    assert is_vector_value(module.output.values[0])
    assert len(module.state_registers) == 9
    register_names = {register.name for register in module.state_registers}
    assert {"body_ttl", "body_mask"} <= register_names
    assert not any(name.startswith("body_pos_") for name in register_names)
    assert not any(name.startswith("body_pixel_") for name in register_names)
    assert len(set(module.state_registers)) == len(module.state_registers)


def test_snake_configuration_validation() -> None:
    with pytest.raises(ValueError, match="positive integer"):
        build_snake_circuit(logical_steps_per_move=0)
    with pytest.raises(ValueError, match="positive integer"):
        build_snake_circuit(logical_steps_per_move=True)
    with pytest.raises(ValueError, match="render_framebuffer"):
        build_snake_circuit(render_framebuffer=1)  # type: ignore[arg-type]

    assert BODY_CAPACITY == CELL_COUNT - 1
    assert MAX_LENGTH == CELL_COUNT
    assert len(FOOD_CELL_IDS) == CELL_COUNT
    assert len(set(FOOD_CELL_IDS)) == CELL_COUNT
