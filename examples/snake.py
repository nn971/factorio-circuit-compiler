"""First playable Snake prototype for the 16x16 lamp-screen device.

The game is deliberately built only from existing compiler primitives:

- the eight-way gate movement detector is consumed as one persistent Level[Vector];
- periodic state uses the compiler-inferred recurrence clock, with an optional logical divider;
- head coordinates, movement/queued directions, score, death state, and body history are registers;
- body positions use a bounded FIFO with a maximum snake length of 16;
- the framebuffer is a persistent 256-lane packed-RGB vector for ``devices.lamp_screen``.

Generate the three blueprints separately, place them in game, and wire:

    movement detector bus -> compiled INPUT movement
    compiled OUTPUT framebuffer -> lamp-screen DISPLAY INPUT

Use only the red or green device bus matching the color printed for the compiled port.
The game waits at the center until the first direction gesture, so it can be wired and powered safely.
The first prototype intentionally uses deterministic food placement and has no restart input yet.
"""

from __future__ import annotations

import argparse
from typing import Final

from factorio_circuit import Circuit, Expr, SignalId, SignalsExpr, compile_circuit
from factorio_circuit.compiler import CompilationResult
from factorio_circuit.devices import DIRECTION_SIGNALS, pixel_signal, rgb
from factorio_circuit.ir.physical import WireColor

SCREEN_WIDTH = 16
SCREEN_HEIGHT = 16
CELL_COUNT = SCREEN_WIDTH * SCREEN_HEIGHT
ORIGIN_X = 8
ORIGIN_Y = 8
BODY_CAPACITY = 15
MAX_LENGTH = BODY_CAPACITY + 1
DEFAULT_LOGICAL_STEPS_PER_MOVE = 1

DIR_E = 0
DIR_S = 1
DIR_W = 2
DIR_N = 3

X_SIGNAL = SignalId("virtual", "signal-X")
Y_SIGNAL = SignalId("virtual", "signal-Y")
DIR_SIGNAL = SignalId("virtual", "signal-D")
QUEUED_DIR_SIGNAL = SignalId("virtual", "signal-R")
PHASE_SIGNAL = SignalId("virtual", "signal-P")
SCORE_SIGNAL = SignalId("virtual", "signal-Q")
DEAD_SIGNAL = SignalId("virtual", "signal-skull")
STARTED_SIGNAL = SignalId("virtual", "signal-check")
POSITION_SIGNAL = SignalId("virtual", "signal-dot")

ARROW_SIGNALS = {
    direction: SignalId("virtual", signal_name)
    for direction, signal_name in DIRECTION_SIGNALS.items()
}

BODY_COLOR = rgb(0x10, 0xA0, 0x20)
HEAD_COLOR = rgb(0x60, 0xFF, 0x70)
FOOD_COLOR = rgb(0xFF, 0x20, 0x20)
DEAD_BODY_COLOR = rgb(0x70, 0x10, 0x10)
DEAD_HEAD_COLOR = rgb(0xFF, 0x50, 0x20)

# Multiplication by an odd number permutes all 256 residues. The offset is chosen so score zero
# starts with food at (11, 8), three eastward moves from the initial head at (8, 8).
FOOD_MULTIPLIER = 73
FOOD_OFFSET = 139


def _cell_id(x: int, y: int) -> int:
    if not 0 <= x < SCREEN_WIDTH or not 0 <= y < SCREEN_HEIGHT:
        raise ValueError(f"cell ({x}, {y}) is outside the Snake board")
    return y * SCREEN_WIDTH + x + 1


def _food_cell_id_for_score(score: int) -> int:
    return (score * FOOD_MULTIPLIER + FOOD_OFFSET) % CELL_COUNT + 1


FOOD_CELL_IDS: tuple[int, ...] = tuple(
    _food_cell_id_for_score(score) for score in range(MAX_LENGTH)
)

# A compact scalar -> one-hot framebuffer decoder. For pixel p the ROM broadcasts cell_id(p)+1.
# Subtracting runtime cell_id makes exactly the selected pixel have count one, which the fixed
# equality filter preserves. This realizes a 256-way decoder with whole-vector operations rather
# than hundreds of coordinate comparators.
PIXEL_ID_ROM: Final[dict[SignalId, int]] = {
    pixel_signal(x, y): _cell_id(x, y) + 1
    for y in range(SCREEN_HEIGHT)
    for x in range(SCREEN_WIDTH)
}


def _or_all(values: list[Expr]) -> Expr:
    """Balanced logical OR reduction to keep recurrence depth logarithmic."""

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


def _lane_value(circuit: Circuit, signal: SignalId, value: Expr | int) -> SignalsExpr:
    return circuit.constant_signals({signal: 1}) * value


def _food_id(score: Expr) -> Expr:
    return (score * FOOD_MULTIPLIER + FOOD_OFFSET) % CELL_COUNT + 1


def _decode_cell_pixels(pixel_id_rom: SignalsExpr, cell_id: Expr) -> SignalsExpr:
    return (pixel_id_rom - cell_id).filter_eq(1)


def _requested_direction(movement: SignalsExpr, old_direction: Expr) -> tuple[Expr, Expr]:
    """Return ``(requested_direction, request_present)`` for the eight-way controller.

    Cardinal regions request that cardinal direction directly. A diagonal region means "turn toward
    the perpendicular component": for example NE requests north while travelling horizontally and
    east while travelling vertically. This makes all eight physical detector regions useful while
    the Snake board itself remains four-neighbour.
    """

    active = {
        direction: movement.signal(signal) != 0 for direction, signal in ARROW_SIGNALS.items()
    }
    present = _or_all(list(active.values()))
    horizontal = (old_direction == DIR_E) | (old_direction == DIR_W)

    ne = horizontal.select(DIR_N, DIR_E)
    se = horizontal.select(DIR_S, DIR_E)
    sw = horizontal.select(DIR_S, DIR_W)
    nw = horizontal.select(DIR_N, DIR_W)

    requested: Expr | int = old_direction
    choices: tuple[tuple[str, Expr | int], ...] = (
        ("NW", nw),
        ("W", DIR_W),
        ("SW", sw),
        ("S", DIR_S),
        ("SE", se),
        ("E", DIR_E),
        ("NE", ne),
        ("N", DIR_N),
    )
    for direction, value in choices:
        requested = active[direction].select(value, requested)
    if not isinstance(requested, Expr):  # pragma: no cover - first select always yields an Expr
        raise AssertionError("direction selector unexpectedly remained constant")
    return requested, present


def _marker_wire_color(result: CompilationResult, marker_entity: int) -> WireColor:
    colors = {
        wire.color
        for wire in result.layout.wires
        if wire.source_entity == marker_entity or wire.target_entity == marker_entity
    }
    if len(colors) != 1:
        rendered = ", ".join(sorted(color.value for color in colors)) or "none"
        raise ValueError(
            f"expected exactly one synthesized wire color at marker {marker_entity}; found {rendered}"
        )
    return next(iter(colors))


def build_snake_circuit(
    *,
    logical_steps_per_move: int = DEFAULT_LOGICAL_STEPS_PER_MOVE,
    render_framebuffer: bool = True,
) -> Circuit:
    """Build the first bounded Snake prototype.

    The snake waits at screen coordinate (8, 8), with initial reference direction east and length one.
    The first direction gesture starts the game and may choose any direction. Each food increases the
    visible/collidable length by one until the fixed maximum length of 16 is reached.
    ``render_framebuffer=False`` keeps the same game state machine but omits the pixel-history state
    and renderer; it exists so contract tests can exercise game semantics cheaply.
    """

    if (
        isinstance(logical_steps_per_move, bool)
        or not isinstance(logical_steps_per_move, int)
        or logical_steps_per_move < 1
    ):
        raise ValueError("logical_steps_per_move must be a positive integer")
    if not isinstance(render_framebuffer, bool):
        raise ValueError("render_framebuffer must be a bool")

    circuit = Circuit("snake_16x16")
    movement = circuit.signals("movement")

    x_reg = circuit.accumulator("head_x")
    y_reg = circuit.accumulator("head_y")
    direction_reg = circuit.freeze("direction")
    queued_direction_reg = circuit.freeze("queued_direction")
    phase_reg = circuit.freeze("move_phase") if logical_steps_per_move > 1 else None
    score_reg = circuit.accumulator("score")
    dead_reg = circuit.freeze("dead")
    started_reg = circuit.freeze("started")

    body_position_regs = [circuit.freeze(f"body_pos_{index}") for index in range(BODY_CAPACITY)]
    body_pixel_regs = (
        [circuit.freeze(f"body_pixel_{index}") for index in range(BODY_CAPACITY)]
        if render_framebuffer
        else []
    )
    pixel_id_rom = circuit.constant_signals(PIXEL_ID_ROM) if render_framebuffer else None

    old_x = x_reg.sample().signal(X_SIGNAL)
    old_y = y_reg.sample().signal(Y_SIGNAL)
    old_direction = direction_reg.sample().signal(DIR_SIGNAL)
    old_queued_direction = queued_direction_reg.sample().signal(QUEUED_DIR_SIGNAL)
    old_score = score_reg.sample().signal(SCORE_SIGNAL)
    old_dead = dead_reg.sample().signal(DEAD_SIGNAL)
    old_started = started_reg.sample().signal(STARTED_SIGNAL)
    old_body_positions = [
        register.sample().signal(POSITION_SIGNAL) for register in body_position_regs
    ]
    old_body_pixels = [register.sample() for register in body_pixel_regs]

    requested_direction, request_present = _requested_direction(movement, old_direction)
    alive = old_dead == 0
    started = old_started != 0
    opposite_direction = (old_direction + 2) % 4
    request_is_legal = started.logical_not() | (requested_direction != opposite_direction)
    accepted_request = alive * request_present * request_is_legal
    queued_direction = accepted_request.select(requested_direction, old_queued_direction)

    dx = (queued_direction == DIR_E) - (queued_direction == DIR_W)
    dy = (queued_direction == DIR_S) - (queued_direction == DIR_N)

    running = alive * (started | accepted_request)
    if phase_reg is None:
        advance = running
    else:
        old_phase = phase_reg.sample().signal(PHASE_SIGNAL)
        advance = running * (old_phase == 0)
        next_phase = (old_phase + 1) % logical_steps_per_move
        phase_reg.set(_lane_value(circuit, PHASE_SIGNAL, next_phase), when=running)
    attempt_move = advance

    next_x = old_x + dx
    next_y = old_y + dy
    next_cell_id = (next_y + ORIGIN_Y) * SCREEN_WIDTH + (next_x + ORIGIN_X) + 1

    wall_collision = (
        (next_x < -ORIGIN_X)
        | (next_x >= SCREEN_WIDTH - ORIGIN_X)
        | (next_y < -ORIGIN_Y)
        | (next_y >= SCREEN_HEIGHT - ORIGIN_Y)
    )
    current_food_id = _food_id(old_score)
    would_eat = attempt_move * wall_collision.logical_not() * (next_cell_id == current_food_id)
    growing = would_eat * (old_score < BODY_CAPACITY)

    self_hits: list[Expr] = []
    for index, position in enumerate(old_body_positions):
        active = old_score > index
        if index + 1 < BODY_CAPACITY:
            is_tail = old_score == index + 1
        else:
            is_tail = old_score >= BODY_CAPACITY
        # The tail vacates on an ordinary move, so moving into its old cell is legal. It remains
        # occupied only while the snake is still below max length and this move eats food.
        checked = active * (growing | is_tail.logical_not())
        self_hits.append(checked * (position == next_cell_id))
    self_collision = _or_all(self_hits)

    collision = attempt_move * (wall_collision | self_collision)
    move_ok = attempt_move * collision.logical_not()
    eat = move_ok * (next_cell_id == current_food_id)

    started_reg.set(circuit.constant_signals({STARTED_SIGNAL: 1}), when=accepted_request)
    queued_direction_reg.set(
        _lane_value(circuit, QUEUED_DIR_SIGNAL, requested_direction), when=accepted_request
    )
    direction_reg.set(_lane_value(circuit, DIR_SIGNAL, queued_direction), when=move_ok)

    x_reg.add(_lane_value(circuit, X_SIGNAL, dx), when=move_ok)
    y_reg.add(_lane_value(circuit, Y_SIGNAL, dy), when=move_ok)
    score_reg.add(circuit.constant_signals({SCORE_SIGNAL: 1}), when=eat)
    dead_reg.set(circuit.constant_signals({DEAD_SIGNAL: 1}), when=collision)

    old_head_cell_id = (old_y + ORIGIN_Y) * SCREEN_WIDTH + (old_x + ORIGIN_X) + 1
    body_position_regs[0].set(
        _lane_value(circuit, POSITION_SIGNAL, old_head_cell_id), when=move_ok
    )
    for index in range(1, BODY_CAPACITY):
        body_position_regs[index].set(
            _lane_value(circuit, POSITION_SIGNAL, old_body_positions[index - 1]), when=move_ok
        )

    if render_framebuffer:
        assert pixel_id_rom is not None
        old_head_pixels = _decode_cell_pixels(pixel_id_rom, old_head_cell_id)
        body_pixel_regs[0].set(old_head_pixels, when=move_ok)
        for index in range(1, BODY_CAPACITY):
            body_pixel_regs[index].set(old_body_pixels[index - 1], when=move_ok)

    # Observe the atomic post-transition state. This compatibility cursor is still the established
    # way to expose "after this game step" values for periodic state programs.
    circuit.step(1)

    head_x = x_reg.sample().signal(X_SIGNAL)
    head_y = y_reg.sample().signal(Y_SIGNAL)
    direction = direction_reg.sample().signal(DIR_SIGNAL)
    score = score_reg.sample().signal(SCORE_SIGNAL)
    dead = dead_reg.sample().signal(DEAD_SIGNAL)
    started_now = started_reg.sample().signal(STARTED_SIGNAL)
    body_positions = [
        register.sample().signal(POSITION_SIGNAL) for register in body_position_regs
    ]
    next_food_id = _food_id(score)

    if render_framebuffer:
        assert pixel_id_rom is not None
        body_pixels = [register.sample() for register in body_pixel_regs]
        head_cell_id = (head_y + ORIGIN_Y) * SCREEN_WIDTH + (head_x + ORIGIN_X) + 1
        head_one_hot = _decode_cell_pixels(pixel_id_rom, head_cell_id)

        occupied_body = circuit.constant_signals({})
        for index, pixels in enumerate(body_pixels):
            occupied_body = occupied_body + pixels.gate(score > index)

        food_occupied_checks = [head_cell_id == next_food_id]
        food_occupied_checks.extend(
            (score > index) * (position == next_food_id)
            for index, position in enumerate(body_positions)
        )
        food_visible = _or_all(food_occupied_checks).logical_not()
        food_one_hot = _decode_cell_pixels(pixel_id_rom, next_food_id).gate(food_visible)

        dead_now = dead != 0
        body_color = dead_now.select(DEAD_BODY_COLOR, BODY_COLOR)
        head_color = dead_now.select(DEAD_HEAD_COLOR, HEAD_COLOR)
        framebuffer = (
            occupied_body * body_color
            + head_one_hot * head_color
            + food_one_hot * FOOD_COLOR
        )
        circuit.output("framebuffer", framebuffer)

    length = (score < BODY_CAPACITY).select(score + 1, MAX_LENGTH)
    circuit.output("score", score)
    circuit.output("length", length)
    circuit.output("dead", dead)
    circuit.output("started", started_now)
    circuit.output("head_x", head_x + ORIGIN_X)
    circuit.output("head_y", head_y + ORIGIN_Y)
    circuit.output("direction", direction)
    circuit.output("food_cell", next_food_id)
    return circuit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--steps-per-move",
        type=int,
        default=DEFAULT_LOGICAL_STEPS_PER_MOVE,
        help=(
            "advance Snake once per this many inferred periodic state occurrences "
            f"(default: {DEFAULT_LOGICAL_STEPS_PER_MOVE})"
        ),
    )
    parser.add_argument(
        "--no-optimize",
        action="store_true",
        help="disable packing optimization during physical synthesis",
    )
    args = parser.parse_args()

    circuit = build_snake_circuit(logical_steps_per_move=args.steps_per_move)
    result = compile_circuit(circuit, optimize=not args.no_optimize)

    movement_port = next(port for port in result.physical_circuit.inputs if port.name == "movement")
    framebuffer_port = next(
        port for port in result.physical_circuit.outputs if port.name == "framebuffer"
    )
    movement_color = _marker_wire_color(result, movement_port.marker_entity)
    framebuffer_color = _marker_wire_color(result, framebuffer_port.marker_entity)

    print(
        "snake: "
        f"combinators={result.physical_circuit.combinator_count}, "
        f"state_period={result.state_timing.uniform_period}"
    )
    print(
        "wire movement detector -> INPUT movement with "
        f"{movement_color.value.upper()}; OUTPUT framebuffer -> display with "
        f"{framebuffer_color.value.upper()}"
    )
    print(result.blueprint_string)


if __name__ == "__main__":
    main()
