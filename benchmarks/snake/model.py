"""Interactive 16x16 Snake workload used by the heavyweight compiler benchmark.

The game is deliberately built only from existing compiler primitives:

- the eight-way gate movement detector is consumed as one persistent Level[Vector];
- a scalar reset Level restores the complete game state atomically;
- periodic state uses the compiler-inferred recurrence clock, with an optional logical divider;
- head coordinates, movement/queued directions, score, death state, and body history are registers;
- body occupancy is represented by two packed 256-lane vectors: per-pixel TTL plus a 0/1 mask;
- the framebuffer is a persistent 256-lane packed-RGB vector for ``devices.lamp_screen``.

Use ``python -m benchmarks.snake.generate`` for the heavyweight physical compile and blueprint
runner.
The model remains separately importable so semantic tests can exercise it without invoking layout.
"""

from __future__ import annotations

from typing import Final

from factorio_circuit import Circuit, Expr, SignalId, SignalsExpr
from factorio_circuit.devices import DIRECTION_SIGNALS, pixel_signal, rgb

SCREEN_WIDTH = 16
SCREEN_HEIGHT = 16
CELL_COUNT = SCREEN_WIDTH * SCREEN_HEIGHT
ORIGIN_X = 8
ORIGIN_Y = 8
BODY_CAPACITY = CELL_COUNT - 1
MAX_LENGTH = CELL_COUNT
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


def _capped_body_count(score: Expr) -> Expr:
    """Return the body-pixel count, capped only by the physical board capacity."""

    return (score < BODY_CAPACITY).select(score, BODY_CAPACITY)


def _contains_pixel(body_mask: SignalsExpr, pixel: SignalsExpr) -> Expr:
    """Test whether a one-hot ``pixel`` is present in a 0/1 body mask.

    Adding two to the queried lane makes it dominate every ordinary body lane. ``max()`` therefore
    selects exactly that lane; a count above two means the body mask also contributed one there.
    """

    return (body_mask + pixel * 2).max().filter_gt(2).any()


def build_snake_circuit(
    *,
    logical_steps_per_move: int = DEFAULT_LOGICAL_STEPS_PER_MOVE,
    render_framebuffer: bool = True,
) -> Circuit:
    """Build the board-bounded Snake prototype with packed time-to-live body state.

    The snake waits at screen coordinate (8, 8), with initial reference direction east and length
    one. The first direction gesture starts the game and may choose any direction. Each food
    increases the visible/collidable length by one until all 256 board cells are occupied.
    A nonzero ``reset`` input restores this complete initial state and wins over movement/collision
    updates on the same logical occurrence.

    Body history uses one TTL vector and one 0/1 occupancy mask across the 256 framebuffer signals.
    TTL value one denotes the tail. On an ordinary move every positive TTL is decremented and the
    old head is inserted with the current body length; on growth the tail is retained and the old
    head is inserted with the new body length. This replaces the previous scalar-position and pixel
    FIFOs.

    ``render_framebuffer=False`` keeps the same game state machine but omits framebuffer
    composition; semantic gameplay tests still exercise the packed body state and collision logic.
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
    reset = circuit.input("reset")
    reset_active = reset != 0
    reset_inactive = reset_active.logical_not()

    x_reg = circuit.accumulator("head_x")
    y_reg = circuit.accumulator("head_y")
    direction_reg = circuit.freeze("direction")
    queued_direction_reg = circuit.freeze("queued_direction")
    phase_reg = circuit.freeze("move_phase") if logical_steps_per_move > 1 else None
    score_reg = circuit.accumulator("score")
    dead_reg = circuit.freeze("dead")
    started_reg = circuit.freeze("started")
    body_ttl_reg = circuit.freeze("body_ttl")
    body_mask_reg = circuit.freeze("body_mask")

    # The packed body state uses framebuffer pixel signals even when display rendering is disabled,
    # so the scalar -> one-hot decoder is part of gameplay rather than a renderer-only helper.
    pixel_id_rom = circuit.constant_signals(PIXEL_ID_ROM)

    old_x = x_reg.sample().signal(X_SIGNAL)
    old_y = y_reg.sample().signal(Y_SIGNAL)
    old_direction = direction_reg.sample().signal(DIR_SIGNAL)
    old_queued_direction = queued_direction_reg.sample().signal(QUEUED_DIR_SIGNAL)
    old_score = score_reg.sample().signal(SCORE_SIGNAL)
    old_dead = dead_reg.sample().signal(DEAD_SIGNAL)
    old_started = started_reg.sample().signal(STARTED_SIGNAL)
    old_body_ttl = body_ttl_reg.sample()
    old_body_mask = body_mask_reg.sample()

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
        phase_reg.set(
            _lane_value(circuit, PHASE_SIGNAL, next_phase).gate(reset_inactive),
            when=running | reset_active,
        )
    attempt_move = advance

    next_x = old_x + dx
    next_y = old_y + dy
    next_cell_id = (next_y + ORIGIN_Y) * SCREEN_WIDTH + (next_x + ORIGIN_X) + 1
    next_head_one_hot = _decode_cell_pixels(pixel_id_rom, next_cell_id)

    wall_collision = (
        (next_x < -ORIGIN_X)
        | (next_x >= SCREEN_WIDTH - ORIGIN_X)
        | (next_y < -ORIGIN_Y)
        | (next_y >= SCREEN_HEIGHT - ORIGIN_Y)
    )
    current_food_id = _food_id(old_score)
    would_eat = attempt_move * wall_collision.logical_not() * (next_cell_id == current_food_id)
    growing = would_eat * (old_score < BODY_CAPACITY)

    # TTL one is the current tail. An ordinary move drops that lane while growth retains it.
    aged_body_ttl = (old_body_ttl - 1).positive()
    tail_mask = old_body_ttl.filter_eq(1)
    not_growing = growing.logical_not()
    collision_body_ttl = old_body_ttl.gate(growing) + aged_body_ttl.gate(not_growing)
    collision_body_mask = old_body_mask.gate(growing) + (old_body_mask - tail_mask).gate(
        not_growing
    )

    self_collision = _contains_pixel(collision_body_mask, next_head_one_hot)

    collision = attempt_move * (wall_collision | self_collision)
    move_ok = attempt_move * collision.logical_not()
    eat = move_ok * (next_cell_id == current_food_id)

    # Freeze registers currently lower one periodic set source each. Fold reset priority into that
    # single source: when reset is active the gated data becomes the empty vector, and the combined
    # control forces that empty vector into the register. This is equivalent to a last-writer reset
    # while preserving the current physical-lowering contract.
    started_reg.set(
        circuit.constant_signals({STARTED_SIGNAL: 1}).gate(reset_inactive),
        when=accepted_request | reset_active,
    )
    queued_direction_reg.set(
        _lane_value(circuit, QUEUED_DIR_SIGNAL, requested_direction).gate(reset_inactive),
        when=accepted_request | reset_active,
    )
    direction_reg.set(
        _lane_value(circuit, DIR_SIGNAL, queued_direction).gate(reset_inactive),
        when=move_ok | reset_active,
    )

    # Accumulator clear suppresses same-occurrence adds in physical lowering, so reset wins even
    # when movement/eating is also active.
    x_reg.add(_lane_value(circuit, X_SIGNAL, dx), when=move_ok)
    x_reg.clear(reset_active)
    y_reg.add(_lane_value(circuit, Y_SIGNAL, dy), when=move_ok)
    y_reg.clear(reset_active)
    score_reg.add(circuit.constant_signals({SCORE_SIGNAL: 1}), when=eat)
    score_reg.clear(reset_active)
    dead_reg.set(
        circuit.constant_signals({DEAD_SIGNAL: 1}).gate(reset_inactive),
        when=collision | reset_active,
    )

    old_head_cell_id = (old_y + ORIGIN_Y) * SCREEN_WIDTH + (old_x + ORIGIN_X) + 1
    old_head_one_hot = _decode_cell_pixels(pixel_id_rom, old_head_cell_id)
    body_count = _capped_body_count(old_score)
    inserted_ttl = growing.select(body_count + 1, body_count)
    inserted_head = old_head_one_hot.gate(inserted_ttl != 0)
    next_body_ttl = collision_body_ttl + old_head_one_hot * inserted_ttl
    next_body_mask = collision_body_mask + inserted_head

    body_ttl_reg.set(
        next_body_ttl.gate(reset_inactive),
        when=move_ok | reset_active,
    )
    body_mask_reg.set(
        next_body_mask.gate(reset_inactive),
        when=move_ok | reset_active,
    )

    # Observe the atomic post-transition state. This compatibility cursor is still the established
    # way to expose "after this game step" values for periodic state programs.
    circuit.step(1)

    head_x = x_reg.sample().signal(X_SIGNAL)
    head_y = y_reg.sample().signal(Y_SIGNAL)
    direction = direction_reg.sample().signal(DIR_SIGNAL)
    score = score_reg.sample().signal(SCORE_SIGNAL)
    dead = dead_reg.sample().signal(DEAD_SIGNAL)
    started_now = started_reg.sample().signal(STARTED_SIGNAL)
    body_mask = body_mask_reg.sample()
    next_food_id = _food_id(score)

    if render_framebuffer:
        head_cell_id = (head_y + ORIGIN_Y) * SCREEN_WIDTH + (head_x + ORIGIN_X) + 1
        head_one_hot = _decode_cell_pixels(pixel_id_rom, head_cell_id)
        food_one_hot = _decode_cell_pixels(pixel_id_rom, next_food_id)

        # Food generated by the deterministic fixture can temporarily land under the snake. Probe
        # the packed occupancy vector directly instead of walking a scalar body-position FIFO.
        food_on_body = _contains_pixel(body_mask, food_one_hot)
        food_on_head = (head_one_hot + food_one_hot * 2).max().filter_gt(2).any()
        food_visible = (food_on_body | food_on_head).logical_not()

        dead_now = dead != 0
        body_color = dead_now.select(DEAD_BODY_COLOR, BODY_COLOR)
        head_color = dead_now.select(DEAD_HEAD_COLOR, HEAD_COLOR)
        framebuffer = (
            body_mask * body_color
            + head_one_hot * head_color
            + food_one_hot.gate(food_visible) * FOOD_COLOR
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
