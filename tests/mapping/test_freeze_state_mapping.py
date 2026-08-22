from dataclasses import replace

import pytest

from factorio_circuit import Circuit, SamplingPolicy, SignalId
from factorio_circuit.lowering.frontend_to_ir import lower_frontend
from factorio_circuit.mapping import (
    MappingProblemError,
    build_periodic_state_mapping_problem,
    ordinary_candidates,
    ordinary_freeze_state_candidates,
    solve_periodic_state_mapping_problem,
    validate_periodic_state_plan,
)

_STATE_SIGNAL = SignalId("virtual", "signal-S")


def _freeze_module():
    circuit = Circuit("freeze_state_mapping")
    enable = circuit.input("enable")
    register = circuit.freeze("memory")
    register.set(
        circuit.constant_signals({_STATE_SIGNAL: 1}),
        when=enable != 0,
    )
    circuit.step(1)
    circuit.output("memory", register.sample())
    return lower_frontend(circuit)


def test_ordinary_freeze_candidate_owns_state_port_timing() -> None:
    problem = build_periodic_state_mapping_problem(
        _freeze_module(),
        period=8,
        output_phases=(15,),
        sampling_policy=SamplingPolicy.ALAP,
    )

    candidates = ordinary_freeze_state_candidates(problem)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.register_name == "memory"
    assert candidate.entity_cost == 4
    assert len(candidate.transition_ports) == 1
    port = candidate.transition_ports[0]
    assert port.value_phase_offset == -1
    assert port.when_phase_offset == -2


def test_freeze_recurrence_is_solved_without_state_timing_plan() -> None:
    pytest.importorskip("ortools.sat.python.cp_model")
    problem = build_periodic_state_mapping_problem(
        _freeze_module(),
        period=8,
        output_phases=(15,),
        sampling_policy=SamplingPolicy.ALAP,
    )

    result = solve_periodic_state_mapping_problem(problem, time_limit_seconds=5.0)

    assert result.proven_optimal
    assert len(result.plan.state_cells) == 1
    cell = result.plan.state_cells[0]
    assert cell.register_name == "memory"
    assert 0 <= cell.base_read_phase < 8
    assert cell.entity_cost == 4
    assert result.plan.entity_cost == 5
    assert result.plan.transport_cost == 0

    transition = problem.state_transitions[0]
    next_read = cell.base_read_phase + 8
    deliveries = {
        (item.consumer, item.operand_index): item for item in result.plan.deliveries
    }
    assert deliveries[(transition.id, 0)].phase == next_read - 1
    assert deliveries[(transition.id, 1)].phase == next_read - 2

    read = problem.state_reads[0]
    sink = problem.sinks[0]
    sink_delivery = deliveries[(sink.id, None)]
    assert sink_delivery.producer == read.id
    assert sink_delivery.phase == 15


def test_freeze_period_one_is_infeasible_for_current_cell_topology() -> None:
    pytest.importorskip("ortools.sat.python.cp_model")
    problem = build_periodic_state_mapping_problem(
        _freeze_module(),
        period=1,
        output_phases=(1,),
        sampling_policy=SamplingPolicy.ALAP,
    )

    with pytest.raises(MappingProblemError, match="failed with status INFEASIBLE"):
        solve_periodic_state_mapping_problem(problem, time_limit_seconds=5.0)


def test_state_plan_validator_rejects_tampered_cell_phase() -> None:
    pytest.importorskip("ortools.sat.python.cp_model")
    problem = build_periodic_state_mapping_problem(
        _freeze_module(),
        period=8,
        output_phases=(15,),
        sampling_policy=SamplingPolicy.ALAP,
    )
    operation_candidates = ordinary_candidates(problem)
    state_candidates = ordinary_freeze_state_candidates(problem)
    result = solve_periodic_state_mapping_problem(
        problem,
        candidates=operation_candidates,
        state_candidates=state_candidates,
        time_limit_seconds=5.0,
    )
    cell = result.plan.state_cells[0]
    tampered = replace(
        result.plan,
        state_cells=(replace(cell, base_read_phase=(cell.base_read_phase + 1) % 8),),
    )

    with pytest.raises(MappingProblemError, match="state-cell .* timing equation"):
        validate_periodic_state_plan(
            problem,
            operation_candidates,
            state_candidates,
            tampered,
        )
