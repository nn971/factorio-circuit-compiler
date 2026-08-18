"""Opt-in heavyweight semantic acceptance checks for the full Snake framebuffer path.

This intentionally lives under ``benchmarks/`` instead of ``tests/``. Building and semantically
simulating the complete 16x16 framebuffer/state graph is useful before accepting Snake-specific
changes, but it is too expensive for routine pytest/CI.
"""

from __future__ import annotations

from benchmarks.snake.model import (
    ARROW_SIGNALS,
    BODY_COLOR,
    FOOD_CELL_IDS,
    FOOD_COLOR,
    HEAD_COLOR,
    build_snake_circuit,
)
from factorio_circuit.devices import pixel_signal
from factorio_circuit.simulate.semantic import LogicalOutput, simulate_stream


def _movement(**directions: int) -> dict[object, int]:
    return {ARROW_SIGNALS[direction]: value for direction, value in directions.items()}


def _simulate_acceptance_trace() -> list[dict[str, LogicalOutput]]:
    """Cover first growth and then reset in one expensive full-framebuffer simulation."""

    module = build_snake_circuit(render_framebuffer=True).build()
    movements = [_movement(E=1), {}, {}, {}]
    resets = [0, 0, 0, 1]
    trace = simulate_stream(
        module,
        [
            {"movement": movement, "reset": reset}
            for movement, reset in zip(movements, resets, strict=True)
        ],
    )
    names = tuple(name for name in module.output.names if name is not None)
    if len(names) != len(module.output.values):
        raise AssertionError("Snake output names/values are inconsistent")
    return [dict(zip(names, row, strict=True)) for row in trace]


def _check_framebuffer_after_first_growth(rows: list[dict[str, LogicalOutput]]) -> None:
    frame = rows[2]["framebuffer"]

    assert isinstance(frame, dict)
    assert frame[pixel_signal(11, 8)] == HEAD_COLOR
    assert frame[pixel_signal(10, 8)] == BODY_COLOR
    assert FOOD_CELL_IDS[1] == 213
    assert frame[pixel_signal(4, 13)] == FOOD_COLOR


def _check_reset_clears_body_framebuffer_history(rows: list[dict[str, LogicalOutput]]) -> None:
    frame = rows[3]["framebuffer"]

    assert isinstance(frame, dict)
    assert frame[pixel_signal(8, 8)] == HEAD_COLOR
    assert frame[pixel_signal(11, 8)] == FOOD_COLOR
    assert frame.get(pixel_signal(10, 8), 0) == 0
    assert rows[3]["score"] == 0
    assert rows[3]["length"] == 1


def main() -> None:
    print("Snake framebuffer semantic acceptance: running full trace")
    rows = _simulate_acceptance_trace()
    _check_framebuffer_after_first_growth(rows)
    _check_reset_clears_body_framebuffer_history(rows)
    print("Snake framebuffer semantic acceptance passed")


if __name__ == "__main__":
    main()
