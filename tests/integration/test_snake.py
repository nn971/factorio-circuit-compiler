from __future__ import annotations

import pytest

from examples.snake import (
    ARROW_SIGNALS,
    BODY_COLOR,
    DIR_E,
    DIR_N,
    FOOD_CELL_IDS,
    FOOD_COLOR,
    HEAD_COLOR,
    MAX_LENGTH,
    build_snake_circuit,
)
from factorio_circuit import compile_circuit
from factorio_circuit.devices import pixel_signal
from factorio_circuit.ir.semantic import is_vector_value
from factorio_circuit.simulate.semantic import LogicalOutput, simulate_stream
from factorio_circuit.synthesis.placement import PlacementOptions


def _movement(**directions: int) -> dict[object, int]:
    return {ARROW_SIGNALS[direction]: value for direction, value in directions.items()}


def _simulate(
    movements: list[dict[object, int]],
    *,
    logical_steps_per_move: int = 1,
    render_framebuffer: bool = False,
) -> list[dict[str, LogicalOutput]]:
    module = build_snake_circuit(
        logical_steps_per_move=logical_steps_per_move,
        render_framebuffer=render_framebuffer,
    ).build()
    trace = simulate_stream(module, [{"movement": row} for row in movements])
    names = tuple(name for name in module.output.names if name is not None)
    assert len(names) == len(module.output.values)
    return [dict(zip(names, row, strict=True)) for row in trace]


def test_snake_waits_at_center_until_first_direction_gesture() -> None:
    rows = _simulate([{}, {}, {}])

    assert all(row["head_x"] == 8 and row["head_y"] == 8 for row in rows)
    assert all(row["started"] == 0 for row in rows)
    assert all(row["dead"] == 0 for row in rows)


def test_snake_first_gesture_can_choose_any_direction() -> None:
    rows = _simulate([_movement(W=1), {}])

    assert rows[0]["started"] == 1
    assert (rows[0]["head_x"], rows[0]["head_y"]) == (7, 8)
    assert rows[1]["head_x"] == 6


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


def test_snake_stops_on_wall_collision() -> None:
    rows = _simulate([_movement(E=1), *({} for _ in range(8))])

    # The east wall is after x=15. The failed move leaves the head at x=15 and latches dead=1.
    assert rows[6]["head_x"] == 15
    assert rows[6]["dead"] == 0
    assert rows[7]["head_x"] == 15
    assert rows[7]["dead"] == 1
    assert rows[8]["head_x"] == 15
    assert rows[8]["dead"] == 1


def test_framebuffer_matches_head_body_and_next_food_after_first_growth() -> None:
    rows = _simulate([_movement(E=1), {}, {}], render_framebuffer=True)
    frame = rows[2]["framebuffer"]

    assert isinstance(frame, dict)
    assert frame[pixel_signal(11, 8)] == HEAD_COLOR
    assert frame[pixel_signal(10, 8)] == BODY_COLOR
    assert FOOD_CELL_IDS[1] == 213
    assert frame[pixel_signal(4, 13)] == FOOD_COLOR


def test_full_snake_build_contains_framebuffer_and_pixel_history() -> None:
    module = build_snake_circuit(render_framebuffer=True).build()

    assert module.output.names[0] == "framebuffer"
    assert is_vector_value(module.output.values[0])
    assert len(module.state_registers) == 37
    assert len(set(module.state_registers)) == len(module.state_registers)


def test_full_snake_compiles_to_a_physical_blueprint() -> None:
    result = compile_circuit(
        build_snake_circuit(render_framebuffer=True),
        optimize=False,
        placement=PlacementOptions(strategy="row"),
    )

    assert result.physical_circuit.combinator_count > 0
    assert result.state_timing.uniform_period is not None
    assert result.blueprint_string.startswith("0")
    blueprint = result.blueprint_json["blueprint"]
    assert isinstance(blueprint, dict)
    assert blueprint["entities"]


def test_snake_configuration_validation() -> None:
    with pytest.raises(ValueError, match="positive integer"):
        build_snake_circuit(logical_steps_per_move=0)
    with pytest.raises(ValueError, match="positive integer"):
        build_snake_circuit(logical_steps_per_move=True)
    with pytest.raises(ValueError, match="render_framebuffer"):
        build_snake_circuit(render_framebuffer=1)  # type: ignore[arg-type]

    assert MAX_LENGTH == 16
