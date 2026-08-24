from math import sqrt

import pytest

from factorio_circuit.blueprint import routing as wire_routing
from factorio_circuit.blueprint.routing import RoutedWire, RoutingPlan, validate_wire_spans
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
    ) -> incremental._FeasibleTopology:
        all_positions = dict(state.positions)
        all_positions.update(state.relay_positions)
        validate_wire_spans(
            topology.routing.wires,
            all_positions,
            maximum_span=state.safe_span,
        )
        observed_relay_counts.append(len(state.relay_positions))
        return original_anneal(state, topology, options)

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
