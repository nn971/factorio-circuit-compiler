from dataclasses import replace

import pytest

from factorio_circuit.ir.physical import (
    Connector,
    ConstantCombinator,
    PhysicalCircuit,
    WireColor,
    WireConnection,
    WireEndpoint,
)
from factorio_circuit.synthesis.fine_routed_refinement import (
    FineRefinementOptions,
    refine_routed_layout_transactionally,
)
from factorio_circuit.synthesis.layout import Layout, LayoutRelay, LayoutWire
from factorio_circuit.synthesis.layout_optimizer import (
    LayoutOptimizationProblem,
    LegalPlacementLattice,
    validate_physical_layout,
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
        "fine-refinement-test",
        entities=[ConstantCombinator(1), ConstantCombinator(2)],
        connections=[_red_connection(1, 2)],
    )


def test_zero_budget_is_exact_routed_pass_through() -> None:
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

    result = refine_routed_layout_transactionally(
        problem,
        options=FineRefinementOptions(proposals=0),
    )

    assert result.layout is layout
    assert result.before == result.after
    assert not result.accepted
    assert result.proposal_budget == 0


def test_fine_refinement_removes_redundant_relay_without_coarse_reseed() -> None:
    circuit = _two_terminal_circuit()
    layout = Layout(
        circuit,
        {1: (0.0, 0.0), 2: (2.0, 0.0), 3: (1.0, 0.0)},
        (LayoutRelay(3, (1.0, 0.0), "redundant relay"),),
        (
            LayoutWire(1, 1, 3, 1, WireColor.RED),
            LayoutWire(3, 1, 2, 1, WireColor.RED),
        ),
        (),
        (),
    )
    problem = LayoutOptimizationProblem(layout, _lattice(), safe_wire_span=3.0)

    result = refine_routed_layout_transactionally(
        problem,
        options=FineRefinementOptions(proposals=1, random_seed=3),
    )

    assert result.accepted
    assert result.after.relay_count == 0
    assert result.after.objective < result.before.objective
    validate_physical_layout(replace(problem, layout=result.layout))


def test_fine_refinement_preserves_fixed_objects() -> None:
    circuit = _two_terminal_circuit()
    layout = Layout(
        circuit,
        {1: (0.0, 0.0), 2: (2.0, 0.0), 3: (1.0, 0.0)},
        (LayoutRelay(3, (1.0, 0.0), "redundant relay"),),
        (
            LayoutWire(1, 1, 3, 1, WireColor.RED),
            LayoutWire(3, 1, 2, 1, WireColor.RED),
        ),
        (),
        (),
    )
    problem = LayoutOptimizationProblem(
        layout,
        _lattice(),
        safe_wire_span=3.0,
        fixed_positions={1: (0.0, 0.0)},
    )

    result = refine_routed_layout_transactionally(
        problem,
        options=FineRefinementOptions(proposals=64, random_seed=11),
    )

    assert result.layout.positions[1] == (0.0, 0.0)
    validate_physical_layout(replace(problem, layout=result.layout))


def test_fine_refinement_is_deterministic_for_fixed_seed() -> None:
    circuit = _two_terminal_circuit()
    layout = Layout(
        circuit,
        {1: (0.0, 0.0), 2: (6.0, 0.0), 3: (2.0, 0.0), 4: (4.0, 0.0)},
        (
            LayoutRelay(3, (2.0, 0.0), "chain a"),
            LayoutRelay(4, (4.0, 0.0), "chain b"),
        ),
        (
            LayoutWire(1, 1, 3, 1, WireColor.RED),
            LayoutWire(3, 1, 4, 1, WireColor.RED),
            LayoutWire(4, 1, 2, 1, WireColor.RED),
        ),
        (),
        (),
    )
    problem = LayoutOptimizationProblem(layout, _lattice(), safe_wire_span=2.1)
    options = FineRefinementOptions(proposals=128, random_seed=17)

    left = refine_routed_layout_transactionally(problem, options=options)
    right = refine_routed_layout_transactionally(problem, options=options)

    assert left == right
    validate_physical_layout(replace(problem, layout=left.layout))


def test_negative_budget_is_rejected() -> None:
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

    with pytest.raises(ValueError, match="non-negative"):
        refine_routed_layout_transactionally(
            problem,
            options=FineRefinementOptions(proposals=-1),
        )
