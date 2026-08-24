from factorio_circuit.blueprint.routing import BlueprintRelay, RoutedWire, RoutingPlan
from factorio_circuit.ir.physical import ConstantCombinator, PhysicalCircuit, WireColor
from factorio_circuit.synthesis.open_vector import (
    _layout_candidate_score,
    _placement_attempt_count,
    _placement_attempt_options,
)
from factorio_circuit.synthesis.placement import PlacementOptions


def test_annealed_retries_keep_one_geometry_and_vary_seed_only() -> None:
    options = PlacementOptions(
        strategy="annealed",
        iterations=250,
        target_fill=0.60,
        corridor_width=4.0,
        restarts=4,
        retry_fill_scale=0.8,
        random_seed=10,
    )

    assert _placement_attempt_count(options) == 4

    attempts = [_placement_attempt_options(options, index) for index in range(4)]
    assert [item.target_fill for item in attempts] == [0.60] * 4
    assert [item.corridor_width for item in attempts] == [4.0] * 4
    assert [item.random_seed for item in attempts] == [10, 11, 12, 13]
    assert all(item.iterations == 250 for item in attempts)
    assert all(item.restarts == 1 for item in attempts)


def test_row_layout_has_only_one_meaningful_attempt() -> None:
    options = PlacementOptions(strategy="row", restarts=5)

    assert _placement_attempt_count(options) == 1


def test_retry_score_prefers_zero_relays_before_compactness() -> None:
    circuit = PhysicalCircuit(
        "retry_score",
        entities=[ConstantCombinator(1), ConstantCombinator(2)],
    )
    no_relays = RoutingPlan(relays=(), wires=())
    compact_with_relay = RoutingPlan(
        relays=(BlueprintRelay(3, (0.5, 1.0), "relay"),),
        wires=(),
    )

    large_zero_relay = _layout_candidate_score(
        circuit,
        {1: (0.0, 0.0), 2: (20.0, 0.0)},
        no_relays,
        restart=0,
    )
    compact_one_relay = _layout_candidate_score(
        circuit,
        {1: (0.0, 0.0), 2: (1.0, 0.0)},
        compact_with_relay,
        restart=1,
    )

    assert large_zero_relay < compact_one_relay


def test_retry_score_uses_area_then_wire_length_as_tiebreaks() -> None:
    circuit = PhysicalCircuit(
        "retry_score",
        entities=[ConstantCombinator(1), ConstantCombinator(2), ConstantCombinator(3)],
    )
    empty = RoutingPlan(relays=(), wires=())
    compact = _layout_candidate_score(
        circuit,
        {1: (0.0, 0.0), 2: (1.0, 0.0), 3: (2.0, 0.0)},
        empty,
        restart=1,
    )
    wide = _layout_candidate_score(
        circuit,
        {1: (0.0, 0.0), 2: (1.0, 0.0), 3: (4.0, 0.0)},
        empty,
        restart=0,
    )
    assert compact < wide

    positions = {1: (0.0, 0.0), 2: (3.0, 0.0), 3: (3.0, 4.0)}
    short_tree = RoutingPlan(
        relays=(),
        wires=(
            RoutedWire(1, 1, 2, 1, WireColor.RED),
            RoutedWire(2, 1, 3, 1, WireColor.RED),
        ),
    )
    long_tree = RoutingPlan(
        relays=(),
        wires=(
            RoutedWire(1, 1, 3, 1, WireColor.RED),
            RoutedWire(2, 1, 3, 1, WireColor.RED),
        ),
    )
    short_score = _layout_candidate_score(circuit, positions, short_tree, restart=1)
    long_score = _layout_candidate_score(circuit, positions, long_tree, restart=0)

    assert short_score < long_score
