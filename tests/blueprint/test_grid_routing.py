from factorio_circuit.blueprint.routing import (
    _chain_is_in_reach,
    _find_grid_relay_positions,
    _relay_candidates_are_clear,
)
from factorio_circuit.progress import CompileProgress


def test_grid_fallback_can_turn_around_a_blocked_straight_lane() -> None:
    source = (0.0, 0.0)
    target = (30.0, 0.0)
    occupied = [((half_step / 2, 0.0), (0.5, 0.5), half_step) for half_step in range(3, 58)]
    updates: list[CompileProgress] = []

    relays = _find_grid_relay_positions(
        source,
        target,
        safe_span=7.0,
        occupied=occupied,
        edge_index=3,
        edge_total=9,
        progress=updates.append,
    )

    assert relays is not None
    assert any(abs(y) > 0.1 for _x, y in relays)
    assert _chain_is_in_reach(source, relays, target, 7.0)
    assert _relay_candidates_are_clear(relays, occupied)
    search_updates = [update for update in updates if update.phase == "routing-search"]
    assert search_updates
    assert search_updates[0].completed == 0
    assert search_updates[0].total is not None
    assert "edge 3/9" in (search_updates[0].detail or "")
