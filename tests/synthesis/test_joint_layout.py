from math import sqrt

import pytest

from factorio_circuit.blueprint import routing as wire_routing
from factorio_circuit.blueprint.routing import (
    BlueprintRelay,
    RoutedWire,
    RoutingPlan,
    validate_wire_spans,
)
from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import ConstantCombinator, PhysicalCircuit, WireColor
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis.incremental_joint_layout import refine_incremental_joint_layout
from factorio_circuit.synthesis.joint_layout import refine_joint_layout
from factorio_circuit.synthesis.placement import PlacementOptions


def _two_terminal_circuit(name: str) -> tuple[abstract.AbstractPhysicalCircuit, PhysicalCircuit]:
    abstract_circuit = abstract.AbstractPhysicalCircuit(name)
    abstract_circuit.entities.extend(
        abstract.ConstantCombinator(entity_id, annotation_only=True) for entity_id in (1, 2)
    )
    abstract_circuit.nets.append(
        abstract.AbstractNet(
            1,
            (),
            tuple(abstract.Endpoint(entity_id, abstract.Connector.SINGLE) for entity_id in (1, 2)),
        )
    )
    physical = PhysicalCircuit(
        name,
        entities=[ConstantCombinator(entity_id, annotation_only=True) for entity_id in (1, 2)],
    )
    return abstract_circuit, physical


def test_joint_layout_shares_one_relay_across_three_terminal_net() -> None:
    abstract_circuit = abstract.AbstractPhysicalCircuit("shared_relay")
    abstract_circuit.entities.extend(
        abstract.ConstantCombinator(entity_id, annotation_only=True) for entity_id in (1, 2, 3)
    )
    abstract_circuit.nets.append(
        abstract.AbstractNet(
            1,
            (),
            tuple(
                abstract.Endpoint(entity_id, abstract.Connector.SINGLE) for entity_id in (1, 2, 3)
            ),
        )
    )
    physical = PhysicalCircuit(
        "shared_relay",
        entities=[ConstantCombinator(entity_id, annotation_only=True) for entity_id in (1, 2, 3)],
    )
    positions = {
        1: (-4.0, 0.0),
        2: (4.0, 0.0),
        3: (0.0, 4.0 * sqrt(3.0)),
    }

    result = refine_joint_layout(
        physical,
        abstract_circuit,
        {1: 1},
        {1: WireColor.RED},
        positions,
        safe_wire_span=7.0,
        options=PlacementOptions(
            iterations=0,
            reserve_corridors=False,
            anchor_io=False,
        ),
    )

    assert len(result.routing.relays) == 1
    assert len(result.routing.wires) == 3
    all_positions = dict(result.positions)
    all_positions.update({relay.entity_id: relay.position for relay in result.routing.relays})
    validate_wire_spans(result.routing.wires, all_positions, maximum_span=7.0)


def test_one_relay_can_carry_independent_red_and_green_net_groups() -> None:
    physical = PhysicalCircuit(
        "dual_color_relay",
        entities=[ConstantCombinator(entity_id) for entity_id in (1, 2, 3, 4)],
    )
    state = incremental.exact._JointState(
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
        positions={1: (-6.0, 0.0), 2: (6.0, 0.0), 3: (0.0, -6.0), 4: (0.0, 6.0)},
        relay_positions={5: (0.0, 0.0)},
        relay_groups={5: frozenset({1, 2})},
        safe_span=7.0,
        forbidden_areas=(),
    )

    incremental.exact._prune_relays(state)
    routing = incremental.exact._routing_plan(state)
    topology = incremental._FeasibleTopology.build(state, routing)

    assert state.relay_groups == {5: frozenset({1, 2})}
    assert len(topology.routing.relays) == 1
    assert [wire.color for wire in topology.routing.wires].count(WireColor.RED) == 2
    assert [wire.color for wire in topology.routing.wires].count(WireColor.GREEN) == 2
    assert "red net 1; green net 2" in topology.routing.relays[0].description


def test_incremental_joint_layout_runs_without_relay_bearing_nets() -> None:
    abstract_circuit, physical = _two_terminal_circuit("incremental_direct")

    result = refine_incremental_joint_layout(
        physical,
        abstract_circuit,
        {1: 1},
        {1: WireColor.RED},
        {1: (-0.5, 0.0), 2: (0.5, 0.0)},
        safe_wire_span=7.0,
        options=PlacementOptions(
            iterations=32,
            reserve_corridors=False,
            anchor_io=False,
            target_fill=0.5,
        ),
    )

    assert result.routing.relays == ()
    assert len(result.routing.wires) == 1
    validate_wire_spans(result.routing.wires, result.positions, maximum_span=7.0)


def test_incremental_joint_layout_is_reach_safe_before_first_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    abstract_circuit, physical = _two_terminal_circuit("incremental_seed")
    observed_relay_counts: list[int] = []
    original_anneal = incremental._anneal_feasible

    def observe_seed(
        state: incremental.exact._JointState,
        topology: incremental._FeasibleTopology,
        options: PlacementOptions,
        grid: incremental.base_placement._GridGeometry,
    ) -> incremental._FeasibleTopology:
        all_positions = dict(state.positions)
        all_positions.update(state.relay_positions)
        validate_wire_spans(
            topology.routing.wires,
            all_positions,
            maximum_span=state.safe_span,
        )
        observed_relay_counts.append(len(state.relay_positions))
        return original_anneal(state, topology, options, grid)

    monkeypatch.setattr(incremental, "_anneal_feasible", observe_seed)
    result = refine_incremental_joint_layout(
        physical,
        abstract_circuit,
        {1: 1},
        {1: WireColor.RED},
        {1: (-0.5, 0.0), 2: (4.5, 0.0)},
        safe_wire_span=3.0,
        options=PlacementOptions(
            iterations=0,
            reserve_corridors=False,
            anchor_io=False,
            target_fill=0.25,
        ),
    )

    assert observed_relay_counts == [1]
    assert len(result.routing.relays) == 1


def test_feasible_topology_rejects_move_that_breaks_reach() -> None:
    abstract_circuit, physical = _two_terminal_circuit("hard_reach")
    endpoints_by_group, colors_by_group = incremental.exact._physical_groups(
        abstract_circuit,
        {1: 1},
        {1: WireColor.RED},
    )
    state = incremental.exact._JointState(
        circuit=physical,
        endpoints_by_group=endpoints_by_group,
        colors_by_group=colors_by_group,
        positions={1: (0.0, 0.0), 2: (2.0, 0.0)},
        relay_positions={},
        relay_groups={},
        safe_span=3.0,
        forbidden_areas=(),
    )
    topology = incremental._FeasibleTopology.build(
        state,
        RoutingPlan(
            relays=(),
            wires=(RoutedWire(1, 1, 2, 1, WireColor.RED),),
        ),
    )

    assert topology.proposal_delta(state, {1: (-2.0, 0.0)}) is None
    assert topology.proposal_delta(state, {1: (0.5, 0.0)}) is not None


def test_local_feasible_simplifier_bypasses_redundant_degree_two_relay() -> None:
    abstract_circuit, physical = _two_terminal_circuit("local_simplify")
    endpoints_by_group, colors_by_group = incremental.exact._physical_groups(
        abstract_circuit,
        {1: 1},
        {1: WireColor.RED},
    )
    state = incremental.exact._JointState(
        circuit=physical,
        endpoints_by_group=endpoints_by_group,
        colors_by_group=colors_by_group,
        positions={1: (0.0, 0.0), 2: (4.0, 0.0)},
        relay_positions={3: (2.0, 0.0)},
        relay_groups={3: frozenset({1})},
        safe_span=5.0,
        forbidden_areas=(),
    )
    topology = incremental._FeasibleTopology.build(
        state,
        RoutingPlan(
            relays=(BlueprintRelay(3, (2.0, 0.0), "relay"),),
            wires=(
                RoutedWire(1, 1, 3, 1, WireColor.RED),
                RoutedWire(3, 1, 2, 1, WireColor.RED),
            ),
        ),
    )

    simplified = incremental._simplify_feasible_topology(state, topology)

    assert state.relay_positions == {}
    assert simplified.routing.relays == ()
    assert len(simplified.routing.wires) == 1
    validate_wire_spans(simplified.routing.wires, state.positions, maximum_span=5.0)


def test_bootstrap_capacity_expands_common_grid_without_changing_corridor_policy() -> None:
    _abstract_circuit, physical = _two_terminal_circuit("workspace_expand")
    options = PlacementOptions(
        target_fill=0.6,
        corridor_width=4.0,
        reserve_corridors=True,
        iterations=100,
        restarts=3,
    )
    initial = incremental.base_placement._candidate_grid(32, 1, options)

    expanded = incremental._expanded_bootstrap_grid(physical, initial, options)

    assert len(expanded.slots) > len(initial.slots)
    assert set(initial.slots).issubset(expanded.slots)
    assert set(initial.unit_slots).issubset(expanded.unit_slots)
    assert expanded.x_positions[: len(initial.x_positions)] == initial.x_positions
    assert expanded.y_positions[: len(initial.y_positions)] == initial.y_positions


def test_incremental_bootstrap_does_not_require_legacy_point_to_point_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    abstract_circuit, physical = _two_terminal_circuit("incremental_repair")

    def legacy_router_must_not_run(*args: object, **kwargs: object) -> object:
        raise AssertionError("incremental joint layout called the legacy relay router")

    monkeypatch.setattr(wire_routing, "_find_relay_positions", legacy_router_must_not_run)
    result = refine_incremental_joint_layout(
        physical,
        abstract_circuit,
        {1: 1},
        {1: WireColor.RED},
        {1: (-0.5, 0.0), 2: (4.5, 0.0)},
        safe_wire_span=3.0,
        options=PlacementOptions(
            iterations=0,
            reserve_corridors=False,
            anchor_io=False,
            target_fill=0.25,
        ),
    )

    assert len(result.routing.relays) == 1
    all_positions = dict(result.positions)
    all_positions.update({relay.entity_id: relay.position for relay in result.routing.relays})
    validate_wire_spans(result.routing.wires, all_positions, maximum_span=3.0)
