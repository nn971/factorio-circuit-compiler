from __future__ import annotations

from benchmarks.langtons_ant.model import (
    ANT_COLOR,
    ARROW_SIGNALS,
    DIR_E,
    DIR_N,
    DIR_S,
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    TRAIL_COLOR,
    ant_step_reference,
    build_langtons_ant_circuit,
)
from factorio_circuit.devices import pixel_signal
from factorio_circuit.simulate.semantic import LogicalOutput, simulate_stream


def _movement(**directions: int) -> dict[object, int]:
    return {ARROW_SIGNALS[direction]: value for direction, value in directions.items()}


def _simulate(movements: list[dict[object, int]], *, render: bool = False) -> list[dict[str, LogicalOutput]]:
    module = build_langtons_ant_circuit(render_framebuffer=render).build()
    trace = simulate_stream(module, [{"movement": movement} for movement in movements])
    names = tuple(name for name in module.output.names if name is not None)
    assert len(names) == len(module.output.values)
    return [dict(zip(names, row, strict=True)) for row in trace]


def _board(row: dict[str, LogicalOutput]) -> dict[object, int]:
    vector = row["board"]
    assert isinstance(vector, dict)
    return vector


def test_langtons_ant_reference_first_steps() -> None:
    board = (0,) * (SCREEN_WIDTH * SCREEN_HEIGHT)

    board, x, y, direction = ant_step_reference(board, 8, 8, DIR_N)
    assert (x, y, direction) == (9, 8, DIR_E)
    assert board[8 * SCREEN_WIDTH + 8] == 1

    board, x, y, direction = ant_step_reference(board, x, y, direction)
    assert (x, y, direction) == (9, 9, DIR_S)
    assert board[8 * SCREEN_WIDTH + 9] == 1


def test_langtons_ant_circuit_builds_expected_outputs() -> None:
    module = build_langtons_ant_circuit(render_framebuffer=False).build()
    assert module.output.names == (
        "board",
        "ant_x",
        "ant_y",
        "direction",
        "running",
        "steps",
    )
    assert tuple(vector_input.name for vector_input in module.vector_inputs) == ("movement",)


def test_langtons_ant_single_step_is_edge_triggered_and_reset_uses_west() -> None:
    rows = _simulate(
        [
            _movement(E=1),
            _movement(E=1),
            {},
            _movement(E=1),
            {},
            _movement(W=1),
        ]
    )

    assert (rows[0]["ant_x"], rows[0]["ant_y"], rows[0]["direction"]) == (9, 8, DIR_E)
    assert rows[0]["steps"] == 1
    assert _board(rows[0]).get(pixel_signal(8, 8), 0) == 1

    # Holding E does not generate a second step.
    assert (rows[1]["ant_x"], rows[1]["ant_y"]) == (9, 8)
    assert rows[1]["steps"] == 1

    # Neutral re-arms, then the second single-step turns south on the new white cell.
    assert (rows[3]["ant_x"], rows[3]["ant_y"], rows[3]["direction"]) == (9, 9, DIR_S)
    assert rows[3]["steps"] == 2
    assert _board(rows[3]).get(pixel_signal(8, 8), 0) == 1
    assert _board(rows[3]).get(pixel_signal(9, 8), 0) == 1

    # W is an atomic reset command.
    assert (rows[5]["ant_x"], rows[5]["ant_y"], rows[5]["direction"]) == (8, 8, DIR_N)
    assert rows[5]["steps"] == 0
    assert rows[5]["running"] == 0
    assert _board(rows[5]) == {}


def test_langtons_ant_run_and_pause_commands_control_periodic_advance() -> None:
    rows = _simulate(
        [
            _movement(N=1),
            {},
            {},
            _movement(S=1),
            {},
        ]
    )

    # N starts running and performs the first step immediately.
    assert rows[0]["running"] == 1
    assert rows[0]["steps"] == 1
    assert rows[1]["steps"] == 2
    assert rows[2]["steps"] == 3

    # S pauses before the next ant transition.
    assert rows[3]["running"] == 0
    assert rows[3]["steps"] == 3
    assert rows[4]["steps"] == 3


def test_langtons_ant_framebuffer_hides_trail_under_ant() -> None:
    rows = _simulate([_movement(E=1)], render=True)
    frame = rows[0]["framebuffer"]
    assert isinstance(frame, dict)

    assert frame[pixel_signal(8, 8)] == TRAIL_COLOR
    assert frame[pixel_signal(9, 8)] == ANT_COLOR
