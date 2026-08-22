import pytest

from factorio_circuit import Circuit, SamplingPolicy, SignalId
from factorio_circuit.lowering.frontend_to_ir import lower_frontend
from factorio_circuit.mapping import (
    DeliveryKind,
    build_periodic_state_mapping_problem,
    ordinary_state_candidates,
    solve_periodic_state_bus_mapping_problem,
    solve_periodic_state_mapping_problem,
)

_LEFT_SIGNAL = SignalId("virtual", "signal-L")
_RIGHT_SIGNAL = SignalId("virtual", "signal-R")


def _two_freeze_problem():
    circuit = Circuit("state_bus_mapping")
    left = circuit.input("left")
    right = circuit.input("right")
    left_memory = circuit.freeze("left_memory")
    right_memory = circuit.freeze("right_memory")
    left_memory.set(
        circuit.constant_signals({_LEFT_SIGNAL: 1}),
        when=left != 0,
    )
    right_memory.set(
        circuit.constant_signals({_RIGHT_SIGNAL: 1}),
        when=right != 0,
    )
    circuit.step(1)
    circuit.output("left_memory", left_memory.sample())
    circuit.output("right_memory", right_memory.sample())
    module = lower_frontend(circuit)
    return build_periodic_state_mapping_problem(
        module,
        period=8,
        output_phases=(15, 15),
        sampling_policy=SamplingPolicy.BEGINNING_OF_STEP,
    )


def test_state_bus_solver_matches_private_baseline_when_disabled() -> None:
    pytest.importorskip("ortools.sat.python.cp_model")
    problem = _two_freeze_problem()
    state_candidates = ordinary_state_candidates(problem)

    baseline = solve_periodic_state_mapping_problem(
        problem,
        state_candidates=state_candidates,
        time_limit_seconds=5.0,
    )
    result = solve_periodic_state_bus_mapping_problem(
        problem,
        state_candidates=state_candidates,
        max_delay_buses=0,
        time_limit_seconds=5.0,
    )

    assert baseline.proven_optimal
    assert result.proven_optimal
    # Two compares + two four-entity Freeze cells + three shared commit entities.
    assert result.plan.entity_cost == baseline.plan.entity_cost == 13
    assert result.plan.periodic_commit is not None
    assert result.plan.periodic_commit.ready_phase == 6
    assert result.plan.transport_cost == baseline.plan.transport_cost == 10
    assert result.plan.total_cost == baseline.plan.total_cost
    assert result.plan.delay_buses == ()
    assert all(
        item.kind is not DeliveryKind.BUS_TRANSPORT for item in result.plan.deliveries
    )


def test_state_bus_solver_selects_same_shared_resource_inside_recurrence() -> None:
    pytest.importorskip("ortools.sat.python.cp_model")
    problem = _two_freeze_problem()

    private = solve_periodic_state_bus_mapping_problem(
        problem,
        max_delay_buses=0,
        time_limit_seconds=5.0,
    )
    shared = solve_periodic_state_bus_mapping_problem(
        problem,
        max_delay_buses=1,
        delay_bus_capacity=2,
        time_limit_seconds=5.0,
    )

    assert private.proven_optimal
    assert shared.proven_optimal
    assert private.plan.entity_cost == shared.plan.entity_cost == 13
    assert private.plan.transport_cost == 10
    assert shared.plan.transport_cost == 7
    assert shared.plan.total_cost == private.plan.total_cost - 3
    assert len(shared.plan.delay_buses) == 1

    bus = shared.plan.delay_buses[0]
    assert len(bus.lanes) == 2
    assert bus.middle_stages == 3
    assert bus.interface_combinators == 4
    assert sum(
        item.kind is DeliveryKind.BUS_TRANSPORT for item in shared.plan.deliveries
    ) == 2
