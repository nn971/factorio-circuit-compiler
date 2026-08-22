import pytest

from factorio_circuit import Circuit, SamplingPolicy, SignalId
from factorio_circuit.lowering.frontend_to_ir import lower_frontend
from factorio_circuit.mapping import (
    build_periodic_state_mapping_problem,
    ordinary_accumulator_state_candidates,
    ordinary_state_candidates,
    solve_periodic_state_mapping_problem,
)

_COUNT_SIGNAL = SignalId("virtual", "signal-C")
_FLAG_SIGNAL = SignalId("virtual", "signal-F")


def _accumulator_module():
    circuit = Circuit("accumulator_state_mapping")
    enable = circuit.input("enable")
    reset = circuit.input("reset")
    register = circuit.accumulator("count")
    register.add(
        circuit.constant_signals({_COUNT_SIGNAL: 1}),
        when=enable != 0,
    )
    register.clear(reset != 0)
    circuit.step(1)
    circuit.output("count", register.sample())
    return lower_frontend(circuit)


def _mixed_module():
    circuit = Circuit("mixed_state_mapping")
    enable = circuit.input("enable")
    reset = circuit.input("reset")
    accumulator = circuit.accumulator("count")
    freeze = circuit.freeze("flag")
    accumulator.add(
        circuit.constant_signals({_COUNT_SIGNAL: 1}),
        when=enable != 0,
    )
    accumulator.clear(reset != 0)
    freeze.set(
        circuit.constant_signals({_FLAG_SIGNAL: 1}),
        when=enable != 0,
    )
    circuit.step(1)
    circuit.output("count", accumulator.sample())
    circuit.output("flag", freeze.sample())
    return lower_frontend(circuit)


def test_ordinary_accumulator_candidate_owns_add_clear_port_timing() -> None:
    problem = build_periodic_state_mapping_problem(
        _accumulator_module(),
        period=8,
        output_phases=(15,),
        sampling_policy=SamplingPolicy.ALAP,
    )

    candidates = ordinary_accumulator_state_candidates(problem)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.register_name == "count"
    assert candidate.entity_cost == 4
    assert candidate.commit_phase_offset == -2
    assert len(candidate.transition_ports) == 2

    ports = {
        problem.state_transition_by_id(port.transition).kind: port
        for port in candidate.transition_ports
    }
    assert ports["add"].value_phase_offset == -1
    assert ports["add"].when_phase_offset == -2
    assert ports["clear"].value_phase_offset is None
    assert ports["clear"].when_phase_offset == -2


def test_accumulator_recurrence_solves_with_candidate_owned_timing() -> None:
    pytest.importorskip("ortools.sat.python.cp_model")
    problem = build_periodic_state_mapping_problem(
        _accumulator_module(),
        period=8,
        output_phases=(15,),
        sampling_policy=SamplingPolicy.ALAP,
    )
    state_candidates = ordinary_state_candidates(problem)

    result = solve_periodic_state_mapping_problem(
        problem,
        state_candidates=state_candidates,
        time_limit_seconds=5.0,
    )

    assert result.proven_optimal
    assert len(result.plan.state_cells) == 1
    cell = result.plan.state_cells[0]
    assert cell.register_name == "count"
    assert 0 <= cell.base_read_phase < 8
    assert cell.entity_cost == 4
    assert result.plan.periodic_commit is not None
    assert result.plan.periodic_commit.entity_cost == 3

    # Two semantic compares + four local state entities + three shared commit entities.
    assert result.plan.entity_cost == 9
    assert result.plan.transport_cost == 0

    transitions = {item.kind: item for item in problem.state_transitions}
    next_read = cell.base_read_phase + 8
    deliveries = {(item.consumer, item.operand_index): item for item in result.plan.deliveries}
    assert deliveries[(transitions["add"].id, 0)].phase == next_read - 1
    assert deliveries[(transitions["add"].id, 1)].phase == next_read - 2
    assert deliveries[(transitions["clear"].id, 1)].phase == next_read - 2


def test_combined_ordinary_state_candidates_cover_mixed_register_families() -> None:
    problem = build_periodic_state_mapping_problem(
        _mixed_module(),
        period=8,
        output_phases=(15, 15),
        sampling_policy=SamplingPolicy.ALAP,
    )

    candidates = ordinary_state_candidates(problem)

    assert {item.register_name for item in candidates} == {"count", "flag"}
    assert len({item.id for item in candidates}) == 2
    costs = {item.register_name: item.entity_cost for item in candidates}
    assert costs == {"count": 4, "flag": 4}
