"""Interactive 4x4 2048 workload rendered on the fixed 16x16 lamp screen.

The benchmark deliberately stresses a different compiler shape from Snake:

- the eight-way player movement detector is consumed as a persistent Level[Vector];
- cardinal gestures are edge-detected, so holding one detector region produces one move;
- NW is a one-shot reset command and the other diagonal regions are neutral/re-arm regions;
- the board is one packed 16-lane whole-vector state value;
- every move evaluates four directional compaction/merge networks and selects the requested result;
- a successful move deterministically spawns one new tile in the first empty row-major cell;
- the 4x4 board is expanded into sixteen 3x3 colored blocks on the 16x16 packed-RGB screen.

Tile values are stored as their ordinary powers of two, so a merge is a multiply-by-two and the
standard 2048 score increment is the value of the merged tile.  Spawn position is deterministic for
benchmark reproducibility; every tenth successful move spawns a 4 instead of a 2.  A later variant
can replace this deterministic source with an oracle without changing the movement kernel.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Literal

from factorio_circuit import Circuit, Expr, SignalId, SignalsExpr
from factorio_circuit.devices import (
    DIRECTION_SIGNALS,
    DISPLAY_VIRTUAL_SIGNAL_POOL,
    pixel_signal,
    rgb,
)

BOARD_WIDTH: Final = 4
BOARD_HEIGHT: Final = 4
CELL_COUNT: Final = BOARD_WIDTH * BOARD_HEIGHT
SCREEN_CELL_SIZE: Final = 4
SCREEN_TILE_SIZE: Final = 3

type Direction = Literal["N", "E", "S", "W"]

DIRECTIONS: Final[tuple[Direction, ...]] = ("N", "E", "S", "W")
BOARD_SIGNALS: Final[tuple[SignalId, ...]] = DISPLAY_VIRTUAL_SIGNAL_POOL[:CELL_COUNT]
ARROW_SIGNALS: Final[dict[str, SignalId]] = {
    direction: SignalId("virtual", signal_name)
    for direction, signal_name in DIRECTION_SIGNALS.items()
}

HELD_SIGNAL: Final = SignalId("virtual", "signal-H")
SCORE_SIGNAL: Final = SignalId("virtual", "signal-Q")
MOVES_SIGNAL: Final = SignalId("virtual", "signal-M")

DEFAULT_INITIAL_BOARD: Final[tuple[int, ...]] = (
    0,
    0,
    0,
    0,
    0,
    2,
    0,
    0,
    0,
    0,
    2,
    0,
    0,
    0,
    0,
    0,
)

EMPTY_COLOR: Final = rgb(0x08, 0x08, 0x0A)
OVERFLOW_COLOR: Final = rgb(0xFF, 0xFF, 0xFF)
TILE_COLORS: Final[dict[int, int]] = {
    2: rgb(0xEE, 0xE4, 0xDA),
    4: rgb(0xED, 0xE0, 0xC8),
    8: rgb(0xF2, 0xB1, 0x79),
    16: rgb(0xF5, 0x95, 0x63),
    32: rgb(0xF6, 0x7C, 0x5F),
    64: rgb(0xF6, 0x5E, 0x3B),
    128: rgb(0xED, 0xCF, 0x72),
    256: rgb(0xED, 0xCC, 0x61),
    512: rgb(0xED, 0xC8, 0x50),
    1024: rgb(0xED, 0xC5, 0x3F),
    2048: rgb(0xED, 0xC2, 0x2E),
    4096: rgb(0xA8, 0x45, 0xD8),
    8192: rgb(0x72, 0x3E, 0xC8),
    16384: rgb(0x38, 0x58, 0xB8),
    32768: rgb(0x24, 0xA0, 0xB8),
}


def _cell_index(x: int, y: int) -> int:
    if not 0 <= x < BOARD_WIDTH or not 0 <= y < BOARD_HEIGHT:
        raise ValueError(f"2048 cell ({x}, {y}) is outside the 4x4 board")
    return y * BOARD_WIDTH + x


CELL_PIXEL_MASKS: Final[tuple[dict[SignalId, int], ...]] = tuple(
    {
        pixel_signal(x * SCREEN_CELL_SIZE + dx, y * SCREEN_CELL_SIZE + dy): 1
        for dy in range(SCREEN_TILE_SIZE)
        for dx in range(SCREEN_TILE_SIZE)
    }
    for y in range(BOARD_HEIGHT)
    for x in range(BOARD_WIDTH)
)


def _validate_board(board: Sequence[int]) -> tuple[int, ...]:
    if len(board) != CELL_COUNT:
        raise ValueError(f"2048 board must contain exactly {CELL_COUNT} cells")
    normalized = tuple(board)
    for value in normalized:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("2048 cells must be non-negative integers")
        if value != 0 and value & (value - 1):
            raise ValueError("nonzero 2048 cells must be powers of two")
    if not any(normalized):
        raise ValueError("2048 initial board must contain at least one tile")
    return normalized


def move_board_reference(
    board: Sequence[int],
    direction: Direction,
) -> tuple[tuple[int, ...], int, bool]:
    """Pure Python 2048 move oracle, before spawning the next tile."""

    cells = list(_validate_board(board))
    if direction not in DIRECTIONS:
        raise ValueError(f"unsupported 2048 direction {direction!r}")

    score_delta = 0
    for indices in _line_indices(direction):
        nonzero = [cells[index] for index in indices if cells[index] != 0]
        merged: list[int] = []
        cursor = 0
        while cursor < len(nonzero):
            if cursor + 1 < len(nonzero) and nonzero[cursor] == nonzero[cursor + 1]:
                value = nonzero[cursor] * 2
                merged.append(value)
                score_delta += value
                cursor += 2
            else:
                merged.append(nonzero[cursor])
                cursor += 1
        merged.extend([0] * (BOARD_WIDTH - len(merged)))
        for index, value in zip(indices, merged, strict=True):
            cells[index] = value

    result = tuple(cells)
    original = tuple(board)
    return result, score_delta, result != original


def apply_move_reference(
    board: Sequence[int],
    direction: Direction,
    *,
    successful_moves_before: int,
) -> tuple[tuple[int, ...], int, bool]:
    """Apply one deterministic benchmark move including the post-move spawn."""

    moved, score_delta, changed = move_board_reference(board, direction)
    if not changed:
        return moved, 0, False
    spawn_value = 4 if (successful_moves_before + 1) % 10 == 0 else 2
    cells = list(moved)
    empty_index = next(index for index, value in enumerate(cells) if value == 0)
    cells[empty_index] = spawn_value
    return tuple(cells), score_delta, True


def _line_indices(direction: Direction) -> tuple[tuple[int, int, int, int], ...]:
    if direction == "W":
        return tuple(
            tuple(_cell_index(x, y) for x in range(BOARD_WIDTH))  # type: ignore[misc]
            for y in range(BOARD_HEIGHT)
        )
    if direction == "E":
        return tuple(
            tuple(_cell_index(x, y) for x in reversed(range(BOARD_WIDTH)))  # type: ignore[misc]
            for y in range(BOARD_HEIGHT)
        )
    if direction == "N":
        return tuple(
            tuple(_cell_index(x, y) for y in range(BOARD_HEIGHT))  # type: ignore[misc]
            for x in range(BOARD_WIDTH)
        )
    return tuple(
        tuple(_cell_index(x, y) for y in reversed(range(BOARD_HEIGHT)))  # type: ignore[misc]
        for x in range(BOARD_WIDTH)
    )


def _or_all(values: Sequence[Expr]) -> Expr:
    if not values:
        raise ValueError("_or_all requires at least one expression")
    layer = list(values)
    while len(layer) > 1:
        next_layer: list[Expr] = []
        for index in range(0, len(layer) - 1, 2):
            next_layer.append(layer[index] | layer[index + 1])
        if len(layer) % 2:
            next_layer.append(layer[-1])
        layer = next_layer
    return layer[0]


def _max_all(values: Sequence[Expr]) -> Expr:
    if not values:
        raise ValueError("_max_all requires at least one expression")
    layer = list(values)
    while len(layer) > 1:
        next_layer: list[Expr] = []
        for index in range(0, len(layer) - 1, 2):
            left = layer[index]
            right = layer[index + 1]
            next_layer.append((left >= right).select(left, right))
        if len(layer) % 2:
            next_layer.append(layer[-1])
        layer = next_layer
    return layer[0]


def _lane_value(circuit: Circuit, signal: SignalId, value: Expr | int) -> SignalsExpr:
    return circuit.constant_signals({signal: 1}) * value


def _pack_cells(circuit: Circuit, cells: Sequence[Expr]) -> SignalsExpr:
    if len(cells) != CELL_COUNT:
        raise ValueError("symbolic 2048 board must contain 16 cells")
    packed = circuit.constant_signals({})
    for signal, value in zip(BOARD_SIGNALS, cells, strict=True):
        packed = packed + _lane_value(circuit, signal, value)
    return packed


def _compact_pair(left: Expr, right: Expr) -> tuple[Expr, Expr]:
    swap = (left == 0) * (right != 0)
    return swap.select(right, left), swap.select(left, right)


def _compact_line(line: Sequence[Expr]) -> tuple[Expr, Expr, Expr, Expr]:
    if len(line) != BOARD_WIDTH:
        raise ValueError("2048 line must contain four cells")
    values = list(line)
    # Stable bubble compaction is intentionally explicit: it creates a useful conditional-routing
    # benchmark rather than hiding the operation in Python elaboration.
    for _ in range(BOARD_WIDTH - 1):
        for index in range(BOARD_WIDTH - 1):
            values[index], values[index + 1] = _compact_pair(values[index], values[index + 1])
    return values[0], values[1], values[2], values[3]


def _move_line(line: Sequence[Expr]) -> tuple[tuple[Expr, Expr, Expr, Expr], Expr]:
    a, b, c, d = _compact_line(line)

    merge_ab = (a != 0) * (a == b)
    merge_bc = merge_ab.logical_not() * (b != 0) * (b == c)
    merge_cd = merge_bc.logical_not() * (c != 0) * (c == d)

    merged = (
        merge_ab.select(a * 2, a),
        merge_ab.select(0, merge_bc.select(b * 2, b)),
        merge_bc.select(0, merge_cd.select(c * 2, c)),
        merge_cd.select(0, d),
    )
    score_delta = merge_ab.select(a * 2, 0) + merge_bc.select(b * 2, 0) + merge_cd.select(c * 2, 0)
    return _compact_line(merged), score_delta


def _move_board(
    cells: Sequence[Expr],
    direction: Direction,
) -> tuple[tuple[Expr, ...], Expr]:
    moved = list(cells)
    line_scores: list[Expr] = []
    for indices in _line_indices(direction):
        line, score_delta = _move_line(tuple(cells[index] for index in indices))
        line_scores.append(score_delta)
        for index, value in zip(indices, line, strict=True):
            moved[index] = value
    score = line_scores[0]
    for value in line_scores[1:]:
        score = score + value
    return tuple(moved), score


def _spawn_first_empty(cells: Sequence[Expr], spawn_value: Expr) -> tuple[Expr, ...]:
    prefix_full: Expr | int = 1
    result: list[Expr] = []
    for cell in cells:
        choose = (cell == 0) * prefix_full
        result.append(cell + choose * spawn_value)
        prefix_full = prefix_full * (cell != 0)
    return tuple(result)


def _can_move(cells: Sequence[Expr]) -> Expr:
    candidates = [cell == 0 for cell in cells]
    for y in range(BOARD_HEIGHT):
        for x in range(BOARD_WIDTH - 1):
            candidates.append(cells[_cell_index(x, y)] == cells[_cell_index(x + 1, y)])
    for y in range(BOARD_HEIGHT - 1):
        for x in range(BOARD_WIDTH):
            candidates.append(cells[_cell_index(x, y)] == cells[_cell_index(x, y + 1)])
    return _or_all(candidates)


def _tile_color(value: Expr) -> Expr:
    color: Expr | int = OVERFLOW_COLOR
    for tile, packed_color in reversed(tuple(TILE_COLORS.items())):
        color = (value == tile).select(packed_color, color)
    color = (value == 0).select(EMPTY_COLOR, color)
    if not isinstance(color, Expr):  # pragma: no cover - first select always creates an Expr
        raise AssertionError("2048 tile color unexpectedly remained constant")
    return color


def build_2048_circuit(
    *,
    initial_board: Sequence[int] = DEFAULT_INITIAL_BOARD,
    render_framebuffer: bool = True,
) -> Circuit:
    """Build the deterministic interactive 2048 benchmark.

    The first logical occurrence seeds ``initial_board``.  A cardinal detector gesture is accepted
    only on entry from a neutral/non-cardinal region, so a held detector signal produces one move.
    NW is an edge-triggered reset.  A successful move spawns into the first empty row-major cell and
    increments ``moves``; unsuccessful moves leave board, score, and move count unchanged.
    """

    initial = _validate_board(initial_board)
    if not isinstance(render_framebuffer, bool):
        raise ValueError("render_framebuffer must be a bool")

    circuit = Circuit("game_2048_4x4")
    movement = circuit.signals("movement")

    board_reg = circuit.freeze("board")
    held_reg = circuit.freeze("gesture_held")
    score_reg = circuit.accumulator("score")
    moves_reg = circuit.accumulator("moves")

    old_board = board_reg.sample()
    old_cells = tuple(old_board.signal(signal) for signal in BOARD_SIGNALS)
    old_held = held_reg.sample().signal(HELD_SIGNAL)
    old_score = score_reg.sample().signal(SCORE_SIGNAL)
    old_moves = moves_reg.sample().signal(MOVES_SIGNAL)

    active = {
        direction: movement.signal(ARROW_SIGNALS[direction]) != 0
        for direction in (*DIRECTIONS, "NW")
    }
    cardinal_present = _or_all([active[direction] for direction in DIRECTIONS])
    command_present = cardinal_present | active["NW"]
    entered_command = command_present * (old_held == 0)
    reset_command = entered_command * active["NW"]
    move_command = entered_command * cardinal_present

    held_reg.set(_lane_value(circuit, HELD_SIGNAL, command_present), when=1)

    boot = old_board.any().logical_not() * (old_moves == 0) * (old_score == 0)
    initial_vector = circuit.constant_signals(
        {signal: value for signal, value in zip(BOARD_SIGNALS, initial, strict=True) if value != 0}
    )

    candidates: dict[Direction, tuple[Expr, ...]] = {}
    candidate_scores: dict[Direction, Expr] = {}
    for direction in DIRECTIONS:
        candidates[direction], candidate_scores[direction] = _move_board(old_cells, direction)

    selected_cells: list[Expr] = []
    for index, old_cell in enumerate(old_cells):
        value = old_cell
        for direction in reversed(DIRECTIONS):
            value = active[direction].select(candidates[direction][index], value)
        selected_cells.append(value)

    selected_score: Expr | int = 0
    for direction in reversed(DIRECTIONS):
        selected_score = active[direction].select(candidate_scores[direction], selected_score)
    if not isinstance(selected_score, Expr):  # pragma: no cover - DIRECTIONS is nonempty
        raise AssertionError("2048 selected score unexpectedly remained constant")

    board_changed = _or_all(
        [new_cell != old_cell for new_cell, old_cell in zip(selected_cells, old_cells, strict=True)]
    )
    valid_move = move_command * board_changed * boot.logical_not() * reset_command.logical_not()

    spawn_value = ((old_moves + 1) % 10 == 0).select(4, 2)
    spawned_cells = _spawn_first_empty(selected_cells, spawn_value)
    moved_vector = _pack_cells(circuit, spawned_cells)
    initialize = boot | reset_command
    board_reg.set(
        initial_vector.gate(initialize) + moved_vector.gate(valid_move),
        when=initialize | valid_move,
    )

    score_reg.add(_lane_value(circuit, SCORE_SIGNAL, selected_score), when=valid_move)
    score_reg.clear(reset_command)
    moves_reg.add(circuit.constant_signals({MOVES_SIGNAL: 1}), when=valid_move)
    moves_reg.clear(reset_command)

    circuit.step(1)

    board = board_reg.sample()
    cells = tuple(board.signal(signal) for signal in BOARD_SIGNALS)
    score = score_reg.sample().signal(SCORE_SIGNAL)
    moves = moves_reg.sample().signal(MOVES_SIGNAL)
    game_over = _can_move(cells).logical_not()
    max_tile = _max_all(cells)

    if render_framebuffer:
        framebuffer = circuit.constant_signals({})
        for mask, value in zip(CELL_PIXEL_MASKS, cells, strict=True):
            framebuffer = framebuffer + circuit.constant_signals(mask) * _tile_color(value)
        circuit.output("framebuffer", framebuffer)

    circuit.output("board", board)
    circuit.output("score", score)
    circuit.output("moves", moves)
    circuit.output("max_tile", max_tile)
    circuit.output("game_over", game_over)
    return circuit
