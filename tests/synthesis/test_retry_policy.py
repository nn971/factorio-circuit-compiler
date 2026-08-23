from factorio_circuit.synthesis.open_vector import (
    _placement_attempt_count,
    _placement_attempt_options,
)
from factorio_circuit.synthesis.placement import PlacementOptions


def test_zero_iteration_greedy_layout_still_uses_deterministic_retries() -> None:
    options = PlacementOptions(
        strategy="annealed",
        iterations=0,
        target_fill=0.60,
        corridor_width=4.0,
        restarts=4,
        retry_fill_scale=0.8,
    )

    assert _placement_attempt_count(options) == 4

    attempts = [_placement_attempt_options(options, index) for index in range(4)]
    assert [round(item.target_fill, 3) for item in attempts] == [0.600, 0.480, 0.384, 0.307]
    assert [round(item.corridor_width, 2) for item in attempts] == [4.00, 5.00, 6.25, 7.81]
    assert all(item.iterations == 0 for item in attempts)
    assert all(item.restarts == 1 for item in attempts)


def test_row_layout_has_only_one_meaningful_attempt() -> None:
    options = PlacementOptions(strategy="row", restarts=5)

    assert _placement_attempt_count(options) == 1
