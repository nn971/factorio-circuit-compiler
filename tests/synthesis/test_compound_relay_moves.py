from __future__ import annotations

from factorio_circuit.blueprint.routing import BlueprintRelay, RoutedWire, RoutingPlan
from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import ConstantCombinator, PhysicalCircuit, WireColor
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis import joint_layout as exact


def _compound_state(
    *,
    right_x: float = 3.0,
    fixed_relay: bool = False,
    blocker: bool = False,
) -> tuple[exact._JointState, incremental._FeasibleTopology]:
    entity_ids = (1, 2, 4) if blocker else (1, 2)
    circuit = PhysicalCircuit(
        "compound_relay_move",
        entities=[ConstantCombinator(entity_id) for entity_id in entity_ids],
    )
    positions = {1: (0.0, 0.0), 2: (right_x, 0.0)}
    if blocker:
        positions[4] = (1.0, 0.0)
    state = exact._JointState(
        circuit=circuit,
        endpoints_by_group={
            1: (
                abstract.Endpoint(1, abstract.Connector.SINGLE),
                abstract.Endpoint(2, abstract.Connector.SINGLE),
            )
        },
        colors_by_group={1: WireColor.RED},
        positions=positions,
        relay_positions={3: (2.0, 0.0)},
        relay_groups={3: frozenset({1})},
        safe_span=2.1,
        forbidden_areas=(),
        fixed_objects=frozenset({3}) if fixed_relay else frozenset(),
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
    return state, topology


def _compound_targets(
    state: exact._JointState,
    topology: incremental._FeasibleTopology,
) -> tuple[dict[int, tuple[float, float]] | None, incremental._SpatialOccupancy]:
    occupancy = incremental._SpatialOccupancy.build(state)
    targets = incremental._terminal_relay_compound_targets(
        state,
        topology,
        1,
        (-1.0, 0.0),
        occupancy,
        {(-1.0, 0.0), (0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)},
    )
    return targets, occupancy


def test_terminal_move_can_translate_one_adjacent_relay_transactionally() -> None:
    state, topology = _compound_state()
    targets, occupancy = _compound_targets(state, topology)

    assert targets == {1: (-1.0, 0.0), 3: (1.0, 0.0)}
    assert topology.proposal_delta(state, targets) is not None

    incremental._apply_compound_targets(state, occupancy, targets)

    assert state.positions[1] == (-1.0, 0.0)
    assert state.relay_positions[3] == (1.0, 0.0)
    assert topology.proposal_delta(state, {}) == 0.0


def test_compound_move_does_not_translate_fixed_relay() -> None:
    state, topology = _compound_state(fixed_relay=True)
    targets, _occupancy = _compound_targets(state, topology)

    assert targets is None


def test_compound_move_rejects_when_relay_far_side_would_break_reach() -> None:
    state, topology = _compound_state(right_x=4.0)
    targets, _occupancy = _compound_targets(state, topology)

    assert targets is None


def test_compound_move_rejects_stationary_geometry_at_relay_target() -> None:
    state, topology = _compound_state(blocker=True)
    targets, _occupancy = _compound_targets(state, topology)

    assert targets is None
