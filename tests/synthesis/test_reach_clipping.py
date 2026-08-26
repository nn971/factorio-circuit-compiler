from __future__ import annotations

import pytest

from factorio_circuit.blueprint.routing import BlueprintRelay, RoutedWire, RoutingPlan
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
        "reach_clipping",
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


def _clip(state, topology, desired):
    grid = _grid()
    occupancy = incremental._SpatialOccupancy.build(state)
    return incremental._reach_clipped_target(
        state,
        topology,
        1,
        desired,
        occupancy,
        grid,
        set(grid.unit_slots),
        set(grid.slots),
    )


def test_continuous_fraction_matches_incident_wire_boundary() -> None:
    state, topology = _state(safe_span=3.1)

    fraction = incremental._maximum_reach_safe_fraction(state, topology, 1, (-3.0, 0.0))

    assert fraction == pytest.approx(1.1 / 3.0)


def test_clipping_finds_furthest_bracketing_reach_safe_grid_step() -> None:
    state, topology = _state(safe_span=3.1)
    desired = (-3.0, 0.0)
    assert topology.proposal_delta(state, {1: desired}) is None

    clipped = _clip(state, topology, desired)

    assert clipped == (-1.0, 0.0)
    assert topology.proposal_delta(state, {1: clipped}) is not None


def test_clipping_returns_none_when_no_forward_grid_step_is_reachable() -> None:
    state, topology = _state(safe_span=2.1)

    assert _clip(state, topology, (-3.0, 0.0)) is None


def test_clipping_does_not_move_into_stationary_geometry() -> None:
    state, topology = _state(safe_span=3.1, blocker=True)

    assert _clip(state, topology, (-3.0, 0.0)) is None


def test_clipping_skips_relay_objects() -> None:
    circuit = PhysicalCircuit("relay_clip", entities=[ConstantCombinator(1)])
    state = exact._JointState(
        circuit=circuit,
        endpoints_by_group={},
        colors_by_group={},
        positions={1: (2.0, 0.0)},
        relay_positions={2: (0.0, 0.0)},
        relay_groups={2: frozenset()},
        safe_span=3.1,
        forbidden_areas=(),
    )
    topology = incremental._FeasibleTopology.build(
        state,
        RoutingPlan(
            relays=(BlueprintRelay(2, (0.0, 0.0), "relay"),),
            wires=(RoutedWire(2, 1, 1, 1, WireColor.RED),),
        ),
    )
    grid = _grid()

    clipped = incremental._reach_clipped_target(
        state,
        topology,
        2,
        (-3.0, 0.0),
        incremental._SpatialOccupancy.build(state),
        grid,
        set(grid.unit_slots),
        set(grid.slots),
    )

    assert clipped is None
