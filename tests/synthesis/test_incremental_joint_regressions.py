from dataclasses import replace

import pytest

from factorio_circuit.blueprint.routing import BlueprintRelay, RoutedWire, RoutingPlan
from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import (
    ConstantCombinator,
    InputPort,
    OutputPort,
    PhysicalCircuit,
    WireColor,
)
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis import joint_layout as exact
from factorio_circuit.synthesis.open_vector import _retryable_layout_error
from factorio_circuit.synthesis.placement import PlacementOptions


def _constant_state_with_relay() -> exact._JointState:
    physical = PhysicalCircuit(
        "relay_snapshot",
        entities=[ConstantCombinator(entity_id) for entity_id in (1, 2)],
    )
    endpoints = (
        abstract.Endpoint(1, abstract.Connector.SINGLE),
        abstract.Endpoint(2, abstract.Connector.SINGLE),
    )
    return exact._JointState(
        circuit=physical,
        endpoints_by_group={1: endpoints},
        colors_by_group={1: WireColor.RED},
        positions={1: (0.0, 0.0), 2: (3.0, 0.0)},
        relay_positions={3: (1.5, 0.0)},
        relay_groups={3: 1},
        safe_span=2.0,
        forbidden_areas=(),
    )


def test_feasible_topology_materializes_current_relay_positions() -> None:
    state = _constant_state_with_relay()
    stale = RoutingPlan(
        relays=(BlueprintRelay(3, (1.0, 0.0), "relay"),),
        wires=(
            RoutedWire(1, 1, 3, 1, WireColor.RED),
            RoutedWire(3, 1, 2, 1, WireColor.RED),
        ),
    )

    topology = incremental._FeasibleTopology.build(state, stale)

    assert topology.routing.relays == (BlueprintRelay(3, (1.5, 0.0), "relay"),)
    incremental._validate_joint_clearance(state, topology.routing)


def test_joint_clearance_rejects_a_stale_serialized_relay_position() -> None:
    state = _constant_state_with_relay()
    stale = RoutingPlan(
        relays=(BlueprintRelay(3, (1.0, 0.0), "relay"),),
        wires=(
            RoutedWire(1, 1, 3, 1, WireColor.RED),
            RoutedWire(3, 1, 2, 1, WireColor.RED),
        ),
    )

    with pytest.raises(ValueError, match="relay positions disagree"):
        incremental._validate_joint_clearance(state, stale)


def test_porous_bootstrap_reanchors_automatic_io_after_grid_expansion() -> None:
    physical = PhysicalCircuit(
        "expanded_io",
        entities=[ConstantCombinator(entity_id, annotation_only=True) for entity_id in (1, 2, 3)],
        inputs=[InputPort("in", 1, None)],
        outputs=[OutputPort("out", 2, None, 0)],
    )
    options = PlacementOptions(
        iterations=0,
        reserve_corridors=False,
        target_fill=0.6,
    )
    initial = incremental.base_placement._candidate_grid(4, 1, options)
    expanded = incremental._expanded_bootstrap_grid(physical, initial, options)
    initial_io = incremental.base_placement._automatic_io_anchors(physical, initial.bounds)
    expanded_io = incremental.base_placement._automatic_io_anchors(physical, expanded.bounds)
    preferred = {**initial_io, 3: initial.unit_slots[0]}

    positions = incremental._porous_bootstrap_positions(
        physical,
        preferred,
        options,
        expanded,
    )

    assert positions is not None
    assert positions[1] == expanded_io[1]
    assert positions[2] == expanded_io[2]
    assert positions[2] != initial_io[2]


def test_porous_bootstrap_preserves_explicit_io_anchor_after_expansion() -> None:
    explicit_output = (100.0, 12.0)
    physical = PhysicalCircuit(
        "expanded_explicit_io",
        entities=[ConstantCombinator(entity_id, annotation_only=True) for entity_id in (1, 2, 3)],
        inputs=[InputPort("in", 1, None)],
        outputs=[OutputPort("out", 2, None, 0)],
    )
    base_options = PlacementOptions(
        iterations=0,
        reserve_corridors=False,
        target_fill=0.6,
    )
    initial = incremental.base_placement._candidate_grid(4, 1, base_options)
    expanded = incremental._expanded_bootstrap_grid(physical, initial, base_options)
    options = replace(base_options, anchors={2: explicit_output})
    preferred = {
        **incremental.base_placement._automatic_io_anchors(physical, initial.bounds),
        2: explicit_output,
        3: initial.unit_slots[0],
    }

    positions = incremental._porous_bootstrap_positions(
        physical,
        preferred,
        options,
        expanded,
    )

    assert positions is not None
    assert positions[2] == explicit_output


def test_bootstrap_rips_up_and_reorders_after_an_early_net_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    physical = PhysicalCircuit(
        "bootstrap_reorder",
        entities=[ConstantCombinator(entity_id) for entity_id in (1, 2, 3, 4)],
    )
    state = exact._JointState(
        circuit=physical,
        endpoints_by_group={
            1: (
                abstract.Endpoint(1, abstract.Connector.SINGLE),
                abstract.Endpoint(2, abstract.Connector.SINGLE),
            ),
            2: (
                abstract.Endpoint(3, abstract.Connector.SINGLE),
                abstract.Endpoint(4, abstract.Connector.SINGLE),
            ),
        },
        colors_by_group={1: WireColor.RED, 2: WireColor.GREEN},
        positions={
            1: (-0.5, 0.0),
            2: (1.5, 0.0),
            3: (-0.5, 2.0),
            4: (1.5, 2.0),
        },
        relay_positions={},
        relay_groups={},
        safe_span=1.1,
        forbidden_areas=(),
    )
    options = PlacementOptions(
        anchor_io=False,
        reserve_corridors=False,
        target_fill=0.5,
        iterations=0,
    )
    grid = incremental.base_placement._candidate_grid(8, 1, options)
    calls: list[int] = []

    def fake_chain(
        _state: exact._JointState,
        group: int,
        _left: int,
        _right: int,
        _free_sites: set[tuple[float, float]],
        _workspace: incremental._RelayWorkspace,
    ) -> tuple[tuple[float, float], ...] | None:
        calls.append(group)
        if len(calls) == 1:
            return None
        return ((0.5, 0.0),) if group == 1 else ((0.5, 2.0),)

    monkeypatch.setattr(incremental, "_find_relay_chain", fake_chain)

    topology = incremental._construct_feasible_bootstrap(state, grid)

    assert calls == [1, 2, 1]
    assert len(state.relay_positions) == 2
    assert len(topology.routing.relays) == 2


def test_new_joint_bootstrap_failure_is_retryable() -> None:
    error = ValueError(
        "joint bootstrap is outside conservative wire reach or exhausted relay-site capacity"
    )

    assert _retryable_layout_error(error)

def test_simplifier_eliminates_redundant_degree_three_relay() -> None:
    physical = PhysicalCircuit(
        "degree_three_bypass",
        entities=[ConstantCombinator(entity_id) for entity_id in (1, 2, 3)],
    )
    state = exact._JointState(
        circuit=physical,
        endpoints_by_group={
            1: tuple(
                abstract.Endpoint(entity_id, abstract.Connector.SINGLE)
                for entity_id in (1, 2, 3)
            )
        },
        colors_by_group={1: WireColor.RED},
        positions={1: (0.0, 0.0), 2: (2.0, 0.0), 3: (1.0, 1.5)},
        relay_positions={4: (1.0, 0.5)},
        relay_groups={4: 1},
        safe_span=2.1,
        forbidden_areas=(),
    )
    routing = RoutingPlan(
        relays=(BlueprintRelay(4, (1.0, 0.5), "relay"),),
        wires=tuple(
            RoutedWire(entity_id, 1, 4, 1, WireColor.RED)
            for entity_id in (1, 2, 3)
        ),
    )
    topology = incremental._FeasibleTopology.build(state, routing)

    simplified = incremental._simplify_feasible_topology(state, topology)

    assert state.relay_positions == {}
    assert state.relay_groups == {}
    assert simplified.routing.relays == ()
    assert len(simplified.routing.wires) == 2
    assert all(
        incremental._distance(
            state.object_position(wire.source_entity),
            state.object_position(wire.target_entity),
        )
        <= state.safe_span
        for wire in simplified.routing.wires
    )

