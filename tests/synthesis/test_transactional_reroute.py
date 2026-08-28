from dataclasses import replace

from factorio_circuit.ir.physical import (
    Connector,
    ConstantCombinator,
    PhysicalCircuit,
    WireColor,
    WireConnection,
    WireEndpoint,
)
from factorio_circuit.synthesis.layout import Layout, LayoutRelay, LayoutWire
from factorio_circuit.synthesis.layout_optimizer import (
    LayoutOptimizationProblem,
    LegalPlacementLattice,
    validate_physical_layout,
)
from factorio_circuit.synthesis.transactional_reroute import (
    reroute_implementation_transactionally,
)


def _lattice(width: int = 12, height: int = 3) -> LegalPlacementLattice:
    unit = tuple((float(x), float(y)) for y in range(height) for x in range(width))
    return LegalPlacementLattice(unit_sites=unit, wide_sites=())


def _red_connection(left: int, right: int) -> WireConnection:
    return WireConnection(
        WireEndpoint(left, Connector.SINGLE),
        WireEndpoint(right, Connector.SINGLE),
        WireColor.RED,
    )


def _two_terminal_circuit() -> PhysicalCircuit:
    return PhysicalCircuit(
        "transactional-reroute-test",
        entities=[ConstantCombinator(1), ConstantCombinator(2)],
        connections=[_red_connection(1, 2)],
    )


def test_transaction_discards_redundant_old_relay_topology() -> None:
    circuit = _two_terminal_circuit()
    layout = Layout(
        circuit,
        {1: (0.0, 0.0), 2: (2.0, 0.0), 3: (1.0, 0.0)},
        (LayoutRelay(3, (1.0, 0.0), "old scaffold"),),
        (
            LayoutWire(1, 1, 3, 1, WireColor.RED),
            LayoutWire(3, 1, 2, 1, WireColor.RED),
        ),
        (),
        (),
    )
    problem = LayoutOptimizationProblem(layout, _lattice(), safe_wire_span=3.0)

    result = reroute_implementation_transactionally(
        problem,
        {1: (0.0, 0.0), 2: (2.0, 0.0)},
    )

    assert result.succeeded
    assert result.failure is None
    assert result.after.relay_count == 0
    assert 3 not in result.layout.positions
    validate_physical_layout(replace(problem, layout=result.layout))


def test_transaction_builds_fresh_relays_for_a_new_long_placement() -> None:
    circuit = _two_terminal_circuit()
    layout = Layout(
        circuit,
        {1: (0.0, 0.0), 2: (2.0, 0.0)},
        (),
        (LayoutWire(1, 1, 2, 1, WireColor.RED),),
        (),
        (),
    )
    problem = LayoutOptimizationProblem(layout, _lattice(), safe_wire_span=2.1)

    result = reroute_implementation_transactionally(
        problem,
        {1: (0.0, 0.0), 2: (6.0, 0.0)},
    )

    assert result.succeeded
    assert result.after.relay_count >= 1
    assert result.layout.positions[1] == (0.0, 0.0)
    assert result.layout.positions[2] == (6.0, 0.0)
    validate_physical_layout(replace(problem, layout=result.layout))


def test_rejected_candidate_returns_exact_valid_fallback() -> None:
    circuit = _two_terminal_circuit()
    layout = Layout(
        circuit,
        {1: (0.0, 0.0), 2: (2.0, 0.0)},
        (),
        (LayoutWire(1, 1, 2, 1, WireColor.RED),),
        (),
        (),
    )
    problem = LayoutOptimizationProblem(layout, _lattice(), safe_wire_span=3.0)

    result = reroute_implementation_transactionally(
        problem,
        {1: (0.0, 0.0), 2: (100.0, 0.0)},
    )

    assert not result.succeeded
    assert result.layout is layout
    assert result.before == result.after
    validate_physical_layout(problem)


def test_transaction_preserves_fixed_implementation_positions() -> None:
    circuit = _two_terminal_circuit()
    layout = Layout(
        circuit,
        {1: (0.0, 0.0), 2: (2.0, 0.0)},
        (),
        (LayoutWire(1, 1, 2, 1, WireColor.RED),),
        (),
        (),
    )
    problem = LayoutOptimizationProblem(
        layout,
        _lattice(),
        safe_wire_span=3.0,
        fixed_positions={1: (0.0, 0.0)},
    )

    result = reroute_implementation_transactionally(
        problem,
        {1: (1.0, 0.0), 2: (2.0, 0.0)},
    )

    assert not result.succeeded
    assert result.layout is layout
    assert result.failure == "candidate moved fixed implementation entity 1"


def test_transactional_rerouting_is_deterministic() -> None:
    circuit = _two_terminal_circuit()
    layout = Layout(
        circuit,
        {1: (0.0, 0.0), 2: (2.0, 0.0)},
        (),
        (LayoutWire(1, 1, 2, 1, WireColor.RED),),
        (),
        (),
    )
    problem = LayoutOptimizationProblem(layout, _lattice(), safe_wire_span=2.1)
    candidate = {1: (0.0, 0.0), 2: (8.0, 0.0)}

    left = reroute_implementation_transactionally(problem, candidate)
    right = reroute_implementation_transactionally(problem, candidate)

    assert left == right
    assert left.succeeded
