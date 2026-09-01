"""Interactive finite 16x16 Langton's Ant benchmark.

The board is a packed 256-lane black/white bit vector using the lamp-screen pixel signal ABI itself.
The ant lives on a toroidal 16x16 surface so the workload runs indefinitely without adding an edge
policy.  The existing eight-way movement detector acts as a one-shot command pad:

- N: run continuously;
- S: pause;
- E: single-step;
- W: reset board, ant pose, run state, and step counter;
- diagonal regions: neutral/re-arm regions.

One ant step dynamically decodes the current cell, reads that lane from the packed board, flips the
lane, turns left on black/right on white, and advances.  This deliberately stresses dynamic one-lane
read/modify/write behavior on a 256-lane state vector, complementing Snake's dense TTL updates.
"""

from __future__ import annotations

from typing import Final, Sequence

from factorio_circuit import Circuit, Expr, SignalId, SignalsExpr
from factorio_circuit.devices import DIRECTION_SIGNALS, SCREEN_HEIGHT, SCREEN_WIDTH, pixel_signal, rgb

CELL_COUNT: Final = SCREEN_WIDTH * SCREEN_HEIGHT
ORIGIN_X: Final = SCREEN_WIDTH // 2
ORIGIN_Y: Final = SCREEN_HEIGHT // 2

DIR_N: Final = 0
DIR_E: Final = 1
DIR_S: Final = 2
DIR_W: Final = 3

X_SIGNAL: Final = SignalId("virtual", "signal-X")
Y_SIGNAL: Final = SignalId("virtual", "signal-Y")
DIR_SIGNAL: Final = SignalId("virtual", "signal-D")
RUNNING_SIGNAL: Final = SignalId("virtual", "signal-R")
HELD_SIGNAL: Final = SignalId("virtual", "signal-H")
STEPS_SIGNAL: Final = SignalId("virtual", "signal-S")

ARROW_SIGNALS: Final[dict[str, SignalId]] = {
    direction: SignalId("virtual", signal_name)
    for direction, signal_name in DIRECTION_SIGNALS.items()
}

TRAIL_COLOR: Final = rgb(0x20, 0x90, 0xD0)
ANT_COLOR: Final = rgb(0xFF, 0x40, 0x20)

PIXEL_ID_ROM: Final[dict[SignalId, int]] = {
    pixel_signal(x, y): y * SCREEN_WIDTH + x + 2
    for y in range(SCREEN_HEIGHT)
    for x in range(SCREEN_WIDTH)
}


def _lane_value(circuit: Circuit, signal: SignalId, value: Expr | int) -> SignalsExpr:
    return circuit.constant_signals({signal: 1}) * value


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


def _decode_cell_pixels(pixel_id_rom: SignalsExpr, cell_id: Expr) -> SignalsExpr:
    return (pixel_id_rom - cell_id).filter_eq(1)


def _contains_pixel(mask: SignalsExpr, pixel: SignalsExpr) -> Expr:
    return (mask + pixel * 2).max().filter_gt(2).any()


def ant_step_reference(
    board: Sequence[int],
    x: int,
    y: int,
    direction: int,
) -> tuple[tuple[int, ...], int, int, int]:
    """Pure Python one-step oracle for the toroidal benchmark."""

    if len(board) != CELL_COUNT:
        raise ValueError(f"Langton board must contain {CELL_COUNT} cells")
    if not 0 <= x < SCREEN_WIDTH or not 0 <= y < SCREEN_HEIGHT:
        raise ValueError("ant coordinates are outside the 16x16 board")
    if direction not in {DIR_N, DIR_E, DIR_S, DIR_W}:
        raise ValueError("ant direction must be in [0, 3]")
    cells = list(board)
    if any(value not in {0, 1} for value in cells):
        raise ValueError("Langton board cells must be binary")

    index = y * SCREEN_WIDTH + x
    black = cells[index] == 1
    cells[index] = 0 if black else 1
    direction = (direction + (3 if black else 1)) % 4
    dx = int(direction == DIR_E) - int(direction == DIR_W)
    dy = int(direction == DIR_S) - int(direction == DIR_N)
    x = (x + dx) % SCREEN_WIDTH
    y = (y + dy) % SCREEN_HEIGHT
    return tuple(cells), x, y, direction


def build_langtons_ant_circuit(*, render_framebuffer: bool = True) -> Circuit:
    """Build the interactive toroidal Langton's Ant workload."""

    if not isinstance(render_framebuffer, bool):
        raise ValueError("render_framebuffer must be a bool")

    circuit = Circuit("langtons_ant_16x16")
    control = circuit.signals("movement")

    board_reg = circuit.freeze("board")
    x_reg = circuit.freeze("ant_x")
    y_reg = circuit.freeze("ant_y")
    direction_reg = circuit.freeze("ant_direction")
    running_reg = circuit.freeze("running")
    held_reg = circuit.freeze("gesture_held")
    steps_reg = circuit.accumulator("steps")

    board = board_reg.sample()
    old_x = x_reg.sample().signal(X_SIGNAL)
    old_y = y_reg.sample().signal(Y_SIGNAL)
    old_direction = direction_reg.sample().signal(DIR_SIGNAL)
    old_running = running_reg.sample().signal(RUNNING_SIGNAL)
    old_held = held_reg.sample().signal(HELD_SIGNAL)

    active = {
        direction: control.signal(ARROW_SIGNALS[direction]) != 0
        for direction in ("N", "E", "S", "W")
    }
    command_present = _or_all(list(active.values()))
    entered = command_present * (old_held == 0)
    run_command = entered * active["N"]
    pause_command = entered * active["S"]
    step_command = entered * active["E"]
    reset_command = entered * active["W"]

    held_reg.set(_lane_value(circuit, HELD_SIGNAL, command_present), when=1)

    next_running = pause_command.select(0, run_command.select(1, old_running))
    running_reg.set(
        _lane_value(circuit, RUNNING_SIGNAL, next_running).gate(reset_command.logical_not()),
        when=run_command | pause_command | reset_command,
    )

    continuous = (old_running | run_command) * pause_command.logical_not()
    advance = (continuous | step_command) * reset_command.logical_not()

    pixel_id_rom = circuit.constant_signals(PIXEL_ID_ROM)
    current_cell_id = (old_y + ORIGIN_Y) * SCREEN_WIDTH + (old_x + ORIGIN_X) + 1
    current_pixel = _decode_cell_pixels(pixel_id_rom, current_cell_id)
    black = _contains_pixel(board, current_pixel)
    flipped_board = board + current_pixel * black.select(-1, 1)

    turned_direction = black.select((old_direction + 3) % 4, (old_direction + 1) % 4)
    dx = (turned_direction == DIR_E) - (turned_direction == DIR_W)
    dy = (turned_direction == DIR_S) - (turned_direction == DIR_N)
    next_x = ((old_x + dx + ORIGIN_X + SCREEN_WIDTH) % SCREEN_WIDTH) - ORIGIN_X
    next_y = ((old_y + dy + ORIGIN_Y + SCREEN_HEIGHT) % SCREEN_HEIGHT) - ORIGIN_Y

    reset_inactive = reset_command.logical_not()
    board_reg.set(flipped_board.gate(advance), when=advance | reset_command)
    x_reg.set(
        _lane_value(circuit, X_SIGNAL, next_x).gate(reset_inactive),
        when=advance | reset_command,
    )
    y_reg.set(
        _lane_value(circuit, Y_SIGNAL, next_y).gate(reset_inactive),
        when=advance | reset_command,
    )
    direction_reg.set(
        _lane_value(circuit, DIR_SIGNAL, turned_direction).gate(reset_inactive),
        when=advance | reset_command,
    )
    steps_reg.add(circuit.constant_signals({STEPS_SIGNAL: 1}), when=advance)
    steps_reg.clear(reset_command)

    circuit.step(1)

    board_now = board_reg.sample()
    x_now = x_reg.sample().signal(X_SIGNAL)
    y_now = y_reg.sample().signal(Y_SIGNAL)
    direction_now = direction_reg.sample().signal(DIR_SIGNAL)
    running_now = running_reg.sample().signal(RUNNING_SIGNAL)
    steps_now = steps_reg.sample().signal(STEPS_SIGNAL)

    if render_framebuffer:
        ant_cell_id = (y_now + ORIGIN_Y) * SCREEN_WIDTH + (x_now + ORIGIN_X) + 1
        ant_pixel = _decode_cell_pixels(pixel_id_rom, ant_cell_id)
        ant_on_black = _contains_pixel(board_now, ant_pixel)
        visible_board = board_now - ant_pixel.gate(ant_on_black)
        framebuffer = visible_board * TRAIL_COLOR + ant_pixel * ANT_COLOR
        circuit.output("framebuffer", framebuffer)

    circuit.output("board", board_now)
    circuit.output("ant_x", x_now + ORIGIN_X)
    circuit.output("ant_y", y_now + ORIGIN_Y)
    circuit.output("direction", direction_now)
    circuit.output("running", running_now)
    circuit.output("steps", steps_now)
    return circuit
