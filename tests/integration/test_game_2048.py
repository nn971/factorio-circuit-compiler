from __future__ import annotations

import pytest

from benchmarks.game_2048.model import (
    ARROW_SIGNALS,
    BOARD_SIGNALS,
    DEFAULT_INITIAL_BOARD,
    apply_move_reference,
    build_2048_circuit,
    move_board_reference,
)
from factorio_circuit.simulate.semantic import LogicalOutput, simulate_stream


def _movement(**directions: int) -> dict[object, int]:
    return {ARROW_SIGNALS[direction]: value for direction, value in directions.items()}


def _board(row: dict[str, LogicalOutput]) -> tuple[int, ...]:
    vector = row["board"]
    assert isinstance(vector, dict)
    return tuple(vector.get(signal, 0) for signal in BOARD_SIGNALS)


def _simulate(
    movements: list[dict[object, int]],
    *,
    initial_board: tuple[int, ...] = DEFAULT_INITIAL_BOARD,
) -> list[dict[str, LogicalOutput]]:
    module = build_2048_circuit(
        initial_board=initial_board,
        render_framebuffer=False,
    ).build()
    trace = simulate_stream(module, [{"movement": movement} for movement in movements])
    names = tuple(name for name in module.output.names if name is not None)
    assert len(names) == len(module.output.values)
    return [dict(zip(names, row, strict=True)) for row in trace]


def test_reference_merge_obeys_standard_2048_pairing() -> None:
    board = (
        2,
        2,
        2,
        2,
        2,
        4,
        4,
        4,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    )

    moved, score, changed = move_board_reference(board, "W")

    assert changed
    assert moved[:4] == (4, 4, 0, 0)
    assert moved[4:8] == (2, 8, 4, 0)
    assert score == 12


def test_reference_spawn_is_deterministic_and_tenth_move_uses_four() -> None:
    board = (
        2,
        2,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    )

    ordinary, ordinary_score, changed = apply_move_reference(
        board,
        "W",
        successful_moves_before=0,
    )
    tenth, tenth_score, tenth_changed = apply_move_reference(
        board,
        "W",
        successful_moves_before=9,
    )

    assert changed and tenth_changed
    assert ordinary[:4] == (4, 2, 0, 0)
    assert tenth[:4] == (4, 4, 0, 0)
    assert ordinary_score == tenth_score == 4


def test_2048_circuit_builds_expected_interactive_outputs() -> None:
    module = build_2048_circuit(render_framebuffer=False).build()
    assert module.output.names == ("board", "score", "moves", "max_tile", "game_over")
    assert tuple(vector_input.name for vector_input in module.vector_inputs) == ("movement",)


@pytest.mark.slow
@pytest.mark.acceptance
def test_2048_symbolic_move_matches_reference_and_held_gesture_is_one_shot() -> None:
    initial = (
        2,
        2,
        4,
        4,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    expected_once, score_once, changed = apply_move_reference(
        initial,
        "W",
        successful_moves_before=0,
    )
    assert changed

    rows = _simulate([{}, _movement(W=1), _movement(W=1), {}], initial_board=initial)

    assert _board(rows[0]) == initial
    assert _board(rows[1]) == expected_once
    assert rows[1]["score"] == score_once
    assert rows[1]["moves"] == 1

    # Remaining in the same detector region does not auto-repeat.
    assert _board(rows[2]) == expected_once
    assert rows[2]["score"] == score_once
    assert rows[2]["moves"] == 1


@pytest.mark.slow
@pytest.mark.acceptance
def test_2048_rearms_after_neutral_and_nw_resets_atomically() -> None:
    initial = (
        2,
        2,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    first, first_score, _ = apply_move_reference(initial, "W", successful_moves_before=0)
    second, second_score, _ = apply_move_reference(first, "E", successful_moves_before=1)

    rows = _simulate(
        [{}, _movement(W=1), {}, _movement(E=1), {}, _movement(NW=1)],
        initial_board=initial,
    )

    assert _board(rows[1]) == first
    assert rows[1]["score"] == first_score
    assert _board(rows[3]) == second
    assert rows[3]["score"] == first_score + second_score
    assert rows[3]["moves"] == 2

    assert _board(rows[5]) == initial
    assert rows[5]["score"] == 0
    assert rows[5]["moves"] == 0


@pytest.mark.slow
@pytest.mark.acceptance
def test_2048_game_over_detects_full_board_without_equal_neighbors() -> None:
    terminal = (
        2,
        4,
        2,
        4,
        4,
        2,
        4,
        2,
        2,
        4,
        2,
        4,
        4,
        2,
        4,
        8,
    )
    rows = _simulate([{}], initial_board=terminal)

    assert _board(rows[0]) == terminal
    assert rows[0]["game_over"] == 1
    assert rows[0]["max_tile"] == 8
