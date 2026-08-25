from dataclasses import replace

import pytest

from benchmarks.snake.generate import _safe_folded_seed_problem
from factorio_circuit.ir.physical import (
    ArithmeticCombinator,
    Connector,
    ConstantCombinator,
    InputPort,
    Operand,
    OutputPort,
    PhysicalCircuit,
    WireColor,
    WireConnection,
    WireEndpoint,
)
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis.layout import Layout, LayoutRelay, LayoutWire
from factorio_circuit.synthesis.layout_optimizer import (
    LayoutOptimizationProblem,
    LegalPlacementLattice,
    optimize_physical_layout,
    physical_layout_metrics,
    validate_physical_layout,
)
from factorio_circuit.synthesis.placement import PlacementOptions


def _lattice(width: int = 12, height: int = 3) -> LegalPlacementLattice:
    unit = tuple((float(x), float(y)) for y in range(height) for x in range(width))
    wide = tuple((float(x) + 0.5, float(y)) for y in range(height) for x in range(width - 1))
    return LegalPlacementLattice(unit_sites=unit, wide_sites=wide)


def _layout(
    circuit: PhysicalCircuit,
    positions: dict[int, tuple[float, float]],
    *,
    relays: tuple[LayoutRelay, ...] = (),
    wires: tuple[LayoutWire, ...] = (),
) -> Layout:
    return Layout(circuit, positions, relays, wires, (), ())


def _red_connection(left: int, right: int) -> WireConnection:
    return WireConnection(
        WireEndpoint(left, Connector.SINGLE),
        WireEndpoint(right, Connector.SINGLE),
        WireColor.RED,
    )


def test_zero_budget_returns_the_exact_valid_input_layout() -> None:
    circuit = PhysicalCircuit(
        "zero_budget",
        entities=[ConstantCombinator(1), ConstantCombinator(2)],
        connections=[_red_connection(1, 2)],
    )
    layout = _layout(
        circuit,
        {1: (0.0, 0.0), 2: (2.0, 0.0)},
        wires=(LayoutWire(1, 1, 2, 1, WireColor.RED),),
    )
    problem = LayoutOptimizationProblem(layout, _lattice(), safe_wire_span=3.0)

    result = optimize_physical_layout(
        problem,
        options=PlacementOptions(anchor_io=False, iterations=0),
    )

    assert result.layout is layout
    assert result.before == result.after == physical_layout_metrics(layout)


def test_safe_folded_benchmark_problem_fixes_public_interface_markers() -> None:
    circuit = PhysicalCircuit(
        "recognizable_interface",
        entities=[
            ConstantCombinator(1, annotation_only=True),
            ConstantCombinator(2, annotation_only=True),
            ConstantCombinator(3),
        ],
        inputs=[InputPort("movement", 1, None)],
        outputs=[OutputPort("framebuffer", 2, None, 0)],
    )
    layout = _layout(circuit, {1: (0.0, 0.0), 2: (3.0, 0.0), 3: (18.0, 9.0)})

    problem = _safe_folded_seed_problem(layout)

    assert problem.fixed_positions == {1: (0.0, 0.0), 2: (3.0, 0.0)}


def test_fixed_public_markers_form_the_optimized_layout_perimeter() -> None:
    body_ids = list(range(3, 15))
    circuit = PhysicalCircuit(
        "front_panel",
        entities=[ConstantCombinator(entity_id) for entity_id in range(1, 15)],
        connections=[_red_connection(1, entity_id) for entity_id in body_ids],
        inputs=[InputPort("control", 1, None)],
        outputs=[OutputPort("display", 2, None, 0)],
    )
    positions = {
        1: (0.0, 0.0),
        2: (3.0, 0.0),
        **{entity_id: (float(entity_id - 3), 8.0) for entity_id in body_ids},
    }
    layout = _layout(
        circuit,
        positions,
        wires=tuple(LayoutWire(1, 1, entity_id, 1, WireColor.RED) for entity_id in body_ids),
    )
    sites = tuple((float(x), float(y)) for y in range(-20, 21) for x in range(-20, 21))
    problem = LayoutOptimizationProblem(
        layout,
        LegalPlacementLattice(unit_sites=sites, wide_sites=()),
        safe_wire_span=100.0,
        fixed_positions={1: positions[1], 2: positions[2]},
    )

    result = optimize_physical_layout(
        problem,
        options=PlacementOptions(anchor_io=False, iterations=256, random_seed=5),
    )

    assert result.layout.positions[1] == (0.0, 0.0)
    assert result.layout.positions[2] == (3.0, 0.0)
    assert min(y for _x, y in result.layout.positions.values()) == 0.0
    assert all(result.layout.positions[entity_id][1] >= 2.0 for entity_id in body_ids)


def test_positive_budget_removes_unnecessary_input_relay_topology() -> None:
    circuit = PhysicalCircuit(
        "relay_bypass",
        entities=[ConstantCombinator(1), ConstantCombinator(2)],
        connections=[_red_connection(1, 2)],
    )
    layout = _layout(
        circuit,
        {1: (0.0, 0.0), 2: (2.0, 0.0), 3: (1.0, 0.0)},
        relays=(LayoutRelay(3, (1.0, 0.0), "generic input relay"),),
        wires=(
            LayoutWire(1, 1, 3, 1, WireColor.RED),
            LayoutWire(3, 1, 2, 1, WireColor.RED),
        ),
    )
    problem = LayoutOptimizationProblem(layout, _lattice(), safe_wire_span=3.0)

    result = optimize_physical_layout(
        problem,
        options=PlacementOptions(anchor_io=False, iterations=1, random_seed=4),
    )

    assert result.after.relay_count == 0
    assert result.after.objective < result.before.objective
    validate_physical_layout(replace(problem, layout=result.layout))


def test_same_circuit_accepts_substantially_different_valid_input_topologies() -> None:
    circuit = PhysicalCircuit(
        "same_circuit_varied_inputs",
        entities=[ConstantCombinator(1), ConstantCombinator(2)],
        connections=[_red_connection(1, 2)],
    )
    layouts = (
        _layout(
            circuit,
            {1: (0.0, 0.0), 2: (2.0, 0.0)},
            wires=(LayoutWire(1, 1, 2, 1, WireColor.RED),),
        ),
        _layout(
            circuit,
            {1: (0.0, 0.0), 2: (2.0, 0.0), 3: (1.0, 0.0)},
            relays=(LayoutRelay(3, (1.0, 0.0), "redundant tree"),),
            wires=(
                LayoutWire(1, 1, 3, 1, WireColor.RED),
                LayoutWire(3, 1, 2, 1, WireColor.RED),
            ),
        ),
        _layout(
            circuit,
            {1: (0.0, 0.0), 2: (6.0, 0.0), 3: (2.0, 0.0), 4: (4.0, 0.0)},
            relays=(
                LayoutRelay(3, (2.0, 0.0), "long tree a"),
                LayoutRelay(4, (4.0, 0.0), "long tree b"),
            ),
            wires=(
                LayoutWire(1, 1, 3, 1, WireColor.RED),
                LayoutWire(3, 1, 4, 1, WireColor.RED),
                LayoutWire(4, 1, 2, 1, WireColor.RED),
            ),
        ),
    )

    for layout in layouts:
        problem = LayoutOptimizationProblem(layout, _lattice(), safe_wire_span=2.1)
        result = optimize_physical_layout(
            problem,
            options=PlacementOptions(anchor_io=False, iterations=1, random_seed=9),
        )
        validate_physical_layout(replace(problem, layout=result.layout))


def test_positive_budget_compacts_a_coarse_unrouted_generic_layout() -> None:
    circuit = PhysicalCircuit(
        "coarse_independent_entities",
        entities=[ConstantCombinator(1), ConstantCombinator(2)],
    )
    layout = _layout(circuit, {1: (0.0, 1.0), 2: (11.0, 1.0)})
    problem = LayoutOptimizationProblem(layout, _lattice(), safe_wire_span=3.0)

    result = optimize_physical_layout(
        problem,
        options=PlacementOptions(anchor_io=False, iterations=512, random_seed=7),
    )

    assert result.after.occupied_area < result.before.occupied_area / 2
    validate_physical_layout(replace(problem, layout=result.layout))


def test_coarse_compaction_clusters_nets_instead_of_replaying_input_row_order() -> None:
    pair_count = 150
    entities = [ConstantCombinator(entity_id) for entity_id in range(1, pair_count * 2 + 1)]
    pairs = tuple((index + 1, pair_count + index + 1) for index in range(pair_count))
    circuit = PhysicalCircuit(
        "adversarial_input_order",
        entities=entities,
        connections=[_red_connection(left, right) for left, right in pairs],
    )
    positions = {
        **{index + 1: (0.0, float(index * 2)) for index in range(pair_count)},
        **{
            pair_count + index + 1: (60.0, float((pair_count - index - 1) * 2))
            for index in range(pair_count)
        },
    }
    layout = _layout(
        circuit,
        positions,
        wires=tuple(LayoutWire(left, 1, right, 1, WireColor.RED) for left, right in pairs),
    )
    problem = LayoutOptimizationProblem(
        layout,
        _lattice(width=61, height=pair_count * 2),
        safe_wire_span=400.0,
    )

    result = optimize_physical_layout(
        problem,
        options=PlacementOptions(anchor_io=False, iterations=256, random_seed=3),
    )

    connected_length = sum(
        incremental._distance(result.layout.positions[left], result.layout.positions[right])
        for left, right in pairs
    )
    assert connected_length / pair_count <= 4.0


def test_coarse_compaction_does_not_reserve_implicit_periodic_channels() -> None:
    entity_count = 120
    entities = [
        ArithmeticCombinator(
            entity_id,
            "+",
            Operand(each=True),
            Operand(constant=1),
            output_each=True,
        )
        for entity_id in range(1, entity_count + 1)
    ]
    circuit = PhysicalCircuit("dense_wide_entities", entities=entities)
    positions = {
        entity_id: (
            float(((entity_id - 1) % 12) * 4) + 0.5,
            float(((entity_id - 1) // 12) * 2),
        )
        for entity_id in range(1, entity_count + 1)
    }
    layout = _layout(circuit, positions)
    problem = LayoutOptimizationProblem(
        layout,
        _lattice(width=80, height=40),
        safe_wire_span=7.0,
    )

    result = optimize_physical_layout(
        problem,
        options=PlacementOptions(anchor_io=False, iterations=256, random_seed=9),
    )

    footprint_area = float(entity_count * 2)
    assert footprint_area / result.after.occupied_area >= 0.70


def test_optimizer_preserves_fixed_implementation_and_relay_positions() -> None:
    circuit = PhysicalCircuit(
        "fixed_objects",
        entities=[ConstantCombinator(1), ConstantCombinator(2)],
        connections=[_red_connection(1, 2)],
    )
    layout = _layout(
        circuit,
        {1: (0.0, 0.0), 2: (4.0, 0.0), 3: (2.0, 0.0)},
        relays=(LayoutRelay(3, (2.0, 0.0), "fixed relay"),),
        wires=(
            LayoutWire(1, 1, 3, 1, WireColor.RED),
            LayoutWire(3, 1, 2, 1, WireColor.RED),
        ),
    )
    problem = LayoutOptimizationProblem(
        layout,
        _lattice(),
        safe_wire_span=3.0,
        fixed_positions={1: (0.0, 0.0), 3: (2.0, 0.0)},
    )

    result = optimize_physical_layout(
        problem,
        options=PlacementOptions(anchor_io=False, iterations=512, random_seed=2),
    )

    assert result.layout.positions[1] == (0.0, 0.0)
    assert result.layout.positions[3] == (2.0, 0.0)
    assert {relay.entity_id for relay in result.layout.relays} == {3}


def test_shared_relay_keeps_red_and_green_networks_distinct() -> None:
    circuit = PhysicalCircuit(
        "shared_colors",
        entities=[ConstantCombinator(entity_id) for entity_id in range(1, 5)],
        connections=[
            _red_connection(1, 2),
            WireConnection(
                WireEndpoint(3, Connector.SINGLE),
                WireEndpoint(4, Connector.SINGLE),
                WireColor.GREEN,
            ),
        ],
    )
    layout = _layout(
        circuit,
        {
            1: (0.0, 0.0),
            2: (4.0, 0.0),
            3: (0.0, 2.0),
            4: (4.0, 2.0),
            5: (2.0, 1.0),
        },
        relays=(LayoutRelay(5, (2.0, 1.0), "shared red/green relay"),),
        wires=(
            LayoutWire(1, 1, 5, 1, WireColor.RED),
            LayoutWire(5, 1, 2, 1, WireColor.RED),
            LayoutWire(3, 2, 5, 2, WireColor.GREEN),
            LayoutWire(5, 2, 4, 2, WireColor.GREEN),
        ),
    )
    problem = LayoutOptimizationProblem(layout, _lattice(), safe_wire_span=3.0)

    validate_physical_layout(problem)
    result = optimize_physical_layout(
        problem,
        options=PlacementOptions(anchor_io=False, iterations=64, random_seed=5),
    )

    validate_physical_layout(replace(problem, layout=result.layout))
    for wire in result.layout.wires:
        if wire.color is WireColor.RED:
            assert wire.source_connector_id % 2 == 1
            assert wire.target_connector_id % 2 == 1
        else:
            assert wire.source_connector_id % 2 == 0
            assert wire.target_connector_id % 2 == 0


def test_validator_rejects_an_electrical_merge_between_distinct_nets() -> None:
    circuit = PhysicalCircuit(
        "merged_nets",
        entities=[ConstantCombinator(entity_id) for entity_id in range(1, 5)],
        connections=[_red_connection(1, 2), _red_connection(3, 4)],
    )
    layout = _layout(
        circuit,
        {1: (0.0, 0.0), 2: (2.0, 0.0), 3: (4.0, 0.0), 4: (6.0, 0.0)},
        wires=(
            LayoutWire(1, 1, 2, 1, WireColor.RED),
            LayoutWire(2, 1, 3, 1, WireColor.RED),
            LayoutWire(3, 1, 4, 1, WireColor.RED),
        ),
    )

    with pytest.raises(ValueError, match="merges distinct physical nets"):
        validate_physical_layout(LayoutOptimizationProblem(layout, _lattice(), safe_wire_span=3.0))


def test_failed_positive_optimization_returns_the_valid_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    circuit = PhysicalCircuit("failed_phase", entities=[ConstantCombinator(1)])
    layout = _layout(circuit, {1: (0.0, 0.0)})
    problem = LayoutOptimizationProblem(layout, _lattice(), safe_wire_span=3.0)

    def fail_phase(*_args: object, **_kwargs: object) -> object:
        raise ValueError("synthetic bounded strategy failure")

    monkeypatch.setattr(incremental, "_anneal_feasible", fail_phase)
    result = optimize_physical_layout(
        problem,
        options=PlacementOptions(anchor_io=False, iterations=32),
    )

    assert result.layout is layout
    assert result.after == result.before
    assert result.diagnostics == (
        "annealing candidate rejected: synthetic bounded strategy failure",
    )


def test_later_failure_returns_an_earlier_valid_coarse_improvement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    circuit = PhysicalCircuit(
        "retain_coarse_best",
        entities=[ConstantCombinator(1), ConstantCombinator(2)],
    )
    layout = _layout(circuit, {1: (0.0, 1.0), 2: (11.0, 1.0)})
    problem = LayoutOptimizationProblem(layout, _lattice(), safe_wire_span=3.0)

    def fail_later_phase(*_args: object, **_kwargs: object) -> object:
        raise ValueError("synthetic failure after coarse compaction")

    monkeypatch.setattr(incremental, "_anneal_feasible", fail_later_phase)
    result = optimize_physical_layout(
        problem,
        options=PlacementOptions(anchor_io=False, iterations=512),
    )

    assert result.layout is not layout
    assert result.after.objective < result.before.objective
    validate_physical_layout(replace(problem, layout=result.layout))
    assert result.diagnostics[-1] == (
        "annealing candidate rejected: synthetic failure after coarse compaction"
    )
