"""Snake variant whose food positions come from a physical random-signal oracle.

The deterministic game state never computes randomness. ``food_candidate`` is a scripted oracle in
reference simulation and is physically provided by a selector combinator in Random Input mode. The
visible food itself remains ordinary deterministic state: eating clears it, then a later logical
occurrence latches one candidate.

The provider candidate mask excludes the current body, current head, and prospective next head. The
deterministic latch also revalidates every proposal against that mask, so a stale physical selector
output can only delay respawn; it can never place food on a blocked cell.
"""

from __future__ import annotations

from factorio_circuit import Circuit, SignalsExpr
from factorio_circuit.devices import pixel_signal

from .model import (
    BODY_CAPACITY,
    BODY_COLOR,
    DEAD_BODY_COLOR,
    DEAD_HEAD_COLOR,
    DEAD_SIGNAL,
    DEFAULT_LOGICAL_STEPS_PER_MOVE,
    DIR_E,
    DIR_N,
    DIR_S,
    DIR_SIGNAL,
    DIR_W,
    FOOD_COLOR,
    HEAD_COLOR,
    MAX_LENGTH,
    ORIGIN_X,
    ORIGIN_Y,
    PHASE_SIGNAL,
    PIXEL_ID_ROM,
    QUEUED_DIR_SIGNAL,
    SCORE_SIGNAL,
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    STARTED_SIGNAL,
    X_SIGNAL,
    Y_SIGNAL,
    _capped_body_count,
    _contains_pixel,
    _decode_cell_pixels,
    _lane_value,
    _requested_direction,
)

FOOD_CANDIDATE_ORACLE = "food_candidate"
FOOD_CANDIDATE_INPUT = "candidates"

ALL_PIXELS = {pixel_signal(x, y): 1 for y in range(SCREEN_HEIGHT) for x in range(SCREEN_WIDTH)}


def build_random_snake_circuit(
    *,
    logical_steps_per_move: int = DEFAULT_LOGICAL_STEPS_PER_MOVE,
    render_framebuffer: bool = True,
) -> Circuit:
    """Build packed-TTL Snake with externally proposed random free-cell food.

    ``food_candidate`` is a vector oracle expected to contain at most one selected pixel signal.
    Physical compilation binds its provider input ``candidates`` to the computed free-cell mask.
    Semantic tests instead provide an explicit oracle trace.

    Food respawn deliberately has a one-logical-occurrence gap. On an eat occurrence the old food is
    cleared; a following occurrence may latch a currently valid candidate. This avoids pretending
    that an opaque physical provider can be sampled after its input changes within one reaction.
    """

    if (
        isinstance(logical_steps_per_move, bool)
        or not isinstance(logical_steps_per_move, int)
        or logical_steps_per_move < 1
    ):
        raise ValueError("logical_steps_per_move must be a positive integer")
    if not isinstance(render_framebuffer, bool):
        raise ValueError("render_framebuffer must be a bool")

    circuit = Circuit("snake_16x16_random_food")
    movement = circuit.signals("movement")
    reset = circuit.input("reset")
    food_candidate = circuit.oracle_signals(FOOD_CANDIDATE_ORACLE)
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
    food_reg = circuit.freeze("food")

    pixel_id_rom = circuit.constant_signals(PIXEL_ID_ROM)
    all_pixels = circuit.constant_signals(ALL_PIXELS)

    old_x = x_reg.sample().signal(X_SIGNAL)
    old_y = y_reg.sample().signal(Y_SIGNAL)
    old_direction = direction_reg.sample().signal(DIR_SIGNAL)
    old_queued_direction = queued_direction_reg.sample().signal(QUEUED_DIR_SIGNAL)
    old_score = score_reg.sample().signal(SCORE_SIGNAL)
    old_dead = dead_reg.sample().signal(DEAD_SIGNAL)
    old_started = started_reg.sample().signal(STARTED_SIGNAL)
    old_body_ttl = body_ttl_reg.sample()
    old_body_mask = body_mask_reg.sample()
    old_food = food_reg.sample()

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

    old_head_cell_id = (old_y + ORIGIN_Y) * SCREEN_WIDTH + (old_x + ORIGIN_X) + 1
    old_head_one_hot = _decode_cell_pixels(pixel_id_rom, old_head_cell_id)

    wall_collision = (
        (next_x < -ORIGIN_X)
        | (next_x >= SCREEN_WIDTH - ORIGIN_X)
        | (next_y < -ORIGIN_Y)
        | (next_y >= SCREEN_HEIGHT - ORIGIN_Y)
    )
    no_wall = wall_collision.logical_not()
    food_hit = _contains_pixel(old_food, next_head_one_hot)
    would_eat = attempt_move * no_wall * food_hit
    growing = would_eat * (old_score < BODY_CAPACITY)

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
    eat = move_ok * food_hit

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

    # Be conservative with respect to target timing. In addition to all currently occupied cells,
    # exclude the cell that an active move is about to enter. The latch below rechecks the selector
    # proposal against this exact mask, so an older invalid proposal is simply ignored.
    prospective_head = next_head_one_hot.gate(attempt_move * no_wall)
    free_pixels: SignalsExpr = (
        all_pixels - old_body_mask - old_head_one_hot - prospective_head
    ).positive()
    circuit.bind_oracle_input(food_candidate, FOOD_CANDIDATE_INPUT, free_pixels)

    food_present = old_food.any()
    candidate_present = food_candidate.any()
    candidate_valid = _contains_pixel(free_pixels, food_candidate)
    need_food = food_present.logical_not()
    load_food = need_food * candidate_present * candidate_valid * reset_inactive
    food_reg.set(
        food_candidate.gate(load_food),
        when=load_food | eat | reset_active,
    )

    circuit.step(1)

    head_x = x_reg.sample().signal(X_SIGNAL)
    head_y = y_reg.sample().signal(Y_SIGNAL)
    direction = direction_reg.sample().signal(DIR_SIGNAL)
    score = score_reg.sample().signal(SCORE_SIGNAL)
    dead = dead_reg.sample().signal(DEAD_SIGNAL)
    started_now = started_reg.sample().signal(STARTED_SIGNAL)
    body_mask = body_mask_reg.sample()
    food = food_reg.sample()

    if render_framebuffer:
        head_cell_id = (head_y + ORIGIN_Y) * SCREEN_WIDTH + (head_x + ORIGIN_X) + 1
        head_one_hot = _decode_cell_pixels(pixel_id_rom, head_cell_id)
        dead_now = dead != 0
        body_color = dead_now.select(DEAD_BODY_COLOR, BODY_COLOR)
        head_color = dead_now.select(DEAD_HEAD_COLOR, HEAD_COLOR)
        framebuffer = body_mask * body_color + head_one_hot * head_color + food * FOOD_COLOR
        circuit.output("framebuffer", framebuffer)

    length = (score < BODY_CAPACITY).select(score + 1, MAX_LENGTH)
    circuit.output("score", score)
    circuit.output("length", length)
    circuit.output("dead", dead)
    circuit.output("started", started_now)
    circuit.output("head_x", head_x + ORIGIN_X)
    circuit.output("head_y", head_y + ORIGIN_Y)
    circuit.output("direction", direction)
    circuit.output("food", food)
    return circuit
