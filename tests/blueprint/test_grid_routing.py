from factorio_circuit.blueprint.routing import (
    _chain_is_in_reach,
    _find_grid_relay_positions,
    _relay_candidates_are_clear,
)


def test_grid_fallback_can_turn_around_a_blocked_straight_lane() -> None:
    source = (0.0, 0.0)
    target = (30.0, 0.0)
    occupied = [((half_step / 2, 0.0), (0.5, 0.5), half_step) for half_step in range(3, 58)]

    relays = _find_grid_relay_positions(
        source,
        target,
        safe_span=7.0,
        occupied=occupied,
    )

    assert relays is not None
    assert any(abs(y) > 0.1 for _x, y in relays)
    assert _chain_is_in_reach(source, relays, target, 7.0)
    assert _relay_candidates_are_clear(relays, occupied)
