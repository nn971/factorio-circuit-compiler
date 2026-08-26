from __future__ import annotations

from factorio_circuit.blueprint.routing import RoutedWire, RoutingPlan
from factorio_circuit.ir.physical import ConstantCombinator, PhysicalCircuit, WireColor
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis import joint_layout as exact
from factorio_circuit.synthesis import placement as base_placement


def _grid() -> base_placement._GridGeometry:
    unit_x = tuple(float(x) for x in range(-3, 4))
    unit_slots = tuple((x, 0.0) for x in unit_x)
    return base_placement._GridGeometry(
        slots=(),
        unit_slots=unit_slots,
        bounds=(-3.5, 3.5, -0.5, 0.5),
        relay_forbidden_areas=(),
        x_positions=(),
        unit_x_positions=unit_x,
        y_positions=(0.0,),
    )


def _state(*, safe_span: float, blocker: bool = False):
    entity_ids = (1, 2, 3) if blocker else (1, 2)
    circuit = PhysicalCircuit(
        "reach_backoff",
        entities=[ConstantCombinator(entity_id) for entity_id in entity_ids],
    )
    positions = {1: (0.0, 0.0), 2: (2.0, 0.0)}
    if blocker:
        positions[3] = (-1.0, 0.0)
    state = exact._JointState(
        circuit=circuit,
        endpoints_by_group={},
        colors_by_group={},
        positions=positions,
        relay_positions={},
        relay_groups={},
        safe_span=safe_span,
        forbidden_areas=(),
    )
    topology = incremental._FeasibleTopology.build(
        state,
        RoutingPlan(
            relays=(),
            wires=(RoutedWire(1, 1, 2, 1, WireColor.RED),),
        ),
    )
    return state, topology


def _repair(state, topology, desired):
    grid = _grid()
    occupancy = incremental._SpatialOccupancy.build(state)
    return incremental._reach_safe_backoff_target(
        state,
        topology,
        1,
        desired,
        occupancy,
        grid,
        set(grid.unit_slots),
        set(grid.slots),
    )


def test_backoff_finds_shorter_reach_safe_step_without_rng() -> None:
    state, topology = _state(safe_span=3.1)
    desired = (-3.0, 0.0)
    assert topology.proposal_delta(state, {1: desired}) is None

    repaired = _repair(state, topology, desired)

    assert repaired is not None
    assert repaired not in {(0.0, 0.0), desired}
    assert topology.proposal_delta(state, {1: repaired}) is not None


def test_backoff_returns_none_when_one_grid_step_already_breaks_reach() -> None:
    state, topology = _state(safe_span=2.1)

    assert _repair(state, topology, (-1.0, 0.0)) is None


def test_backoff_does_not_move_into_stationary_geometry() -> None:
    state, topology = _state(safe_span=3.1, blocker=True)

    assert _repair(state, topology, (-3.0, 0.0)) is None
