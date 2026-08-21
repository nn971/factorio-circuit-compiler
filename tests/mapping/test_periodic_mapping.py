import pytest

from factorio_circuit import Circuit, SamplingPolicy, SignalId
from factorio_circuit.ir.state import VectorRegisterRead
from factorio_circuit.lowering.frontend_to_ir import lower_frontend
from factorio_circuit.mapping import (
    MappingProblemError,
    MappingSourceMode,
    MappingStateRead,
    build_periodic_level_mapping_problem,
    build_periodic_state_mapping_problem,
    build_stateless_level_mapping_problem,
    ordinary_candidates,
    solve_mapping_problem,
)

_STATE_SIGNAL = SignalId("virtual", "signal-S")


def _state_module(*, step_before_output: bool):
    circuit = Circuit("periodic_mapping_extract")
    enable = circuit.input("enable")
    register = circuit.freeze("memory")
    register.set(
        circuit.constant_signals({_STATE_SIGNAL: 1}),
        when=enable != 0,
    )
    if step_before_output:
        circuit.step(1)
    circuit.output("memory", register.sample())
    return lower_frontend(circuit)


def test_periodic_output_extractor_models_register_occurrence_as_boundary_window() -> None:
    module = _state_module(step_before_output=False)

    problem = build_periodic_level_mapping_problem(
        module,
        period=8,
        output_phases=(7,),
        sampling_policy=SamplingPolicy.ALAP,
    )

    assert len(problem.sources) == 1
    assert problem.state_reads == ()
    assert problem.state_transitions == ()
    source = problem.sources[0]
    assert isinstance(source.semantic, VectorRegisterRead)
    assert source.semantic.offset == 0
    assert source.mode is MappingSourceMode.STABLE
    assert (source.start_phase, source.end_phase_exclusive) == (0, 8)
    assert problem.sinks[0].phase == 7
    # Transition value/control cones are deliberately outside this diagnostic output-only problem.
    assert problem.operations == ()


def test_periodic_output_extractor_reindexes_next_register_occurrence() -> None:
    module = _state_module(step_before_output=True)

    problem = build_periodic_level_mapping_problem(
        module,
        period=8,
        output_phases=(15,),
        sampling_policy=SamplingPolicy.ALAP,
    )

    source = problem.sources[0]
    assert isinstance(source.semantic, VectorRegisterRead)
    assert source.semantic.offset == 1
    assert source.mode is MappingSourceMode.STABLE
    assert (source.start_phase, source.end_phase_exclusive) == (8, 16)
    assert problem.horizon == 15


def test_periodic_output_boundary_token_is_free_until_boundary_but_not_through_it() -> None:
    pytest.importorskip("ortools.sat.python.cp_model")
    module = _state_module(step_before_output=True)

    inside = build_periodic_level_mapping_problem(
        module,
        period=8,
        output_phases=(15,),
    )
    boundary = build_periodic_level_mapping_problem(
        module,
        period=8,
        output_phases=(16,),
    )

    inside_result = solve_mapping_problem(inside, time_limit_seconds=5.0)
    boundary_result = solve_mapping_problem(boundary, time_limit_seconds=5.0)

    assert inside_result.proven_optimal
    assert boundary_result.proven_optimal
    assert inside_result.plan.transport_cost == 0
    assert boundary_result.plan.transport_cost == 1
    assert boundary_result.plan.exact_lifetimes[0].start_phase == 15
    assert boundary_result.plan.exact_lifetimes[0].end_phase == 16


def test_full_periodic_state_extractor_keeps_register_read_phase_unresolved() -> None:
    module = _state_module(step_before_output=False)

    problem = build_periodic_state_mapping_problem(
        module,
        period=8,
        output_phases=(7,),
        sampling_policy=SamplingPolicy.ALAP,
    )

    assert len(problem.state_reads) == 1
    read = problem.state_reads[0]
    assert isinstance(read, MappingStateRead)
    assert read.register_name == "memory"
    assert read.logical_offset == 0
    assert not hasattr(read, "start_phase")
    assert problem.sinks[0].value == read.id


def test_full_periodic_state_extractor_records_transition_cones_without_write_phase() -> None:
    module = _state_module(step_before_output=False)

    problem = build_periodic_state_mapping_problem(
        module,
        period=8,
        output_phases=(7,),
        sampling_policy=SamplingPolicy.ALAP,
    )

    assert len(problem.state_transitions) == 1
    transition = problem.state_transitions[0]
    assert transition.register_name == "memory"
    assert transition.kind == "set"
    assert transition.logical_offset == 0
    assert transition.value in problem.value_ids
    assert transition.when in problem.value_ids
    assert not hasattr(transition, "phase")
    # The transition control/value expressions are now part of the semantic mapping graph rather
    # than being hidden behind the established physical StateTimingPlan.
    assert problem.sources
    assert problem.operations


def test_full_state_problem_refuses_physical_use_enumeration_without_state_cell_candidate() -> None:
    module = _state_module(step_before_output=False)
    problem = build_periodic_state_mapping_problem(
        module,
        period=8,
        output_phases=(7,),
        sampling_policy=SamplingPolicy.ALAP,
    )

    with pytest.raises(MappingProblemError, match="state-cell implementation candidates"):
        problem.uses()


def test_current_solver_rejects_full_state_problem_at_state_cell_boundary() -> None:
    pytest.importorskip("ortools.sat.python.cp_model")
    module = _state_module(step_before_output=False)
    problem = build_periodic_state_mapping_problem(
        module,
        period=8,
        output_phases=(7,),
        sampling_policy=SamplingPolicy.ALAP,
    )
    candidates = ordinary_candidates(problem)

    with pytest.raises(MappingProblemError, match="state-cell implementation candidates"):
        solve_mapping_problem(problem, candidates=candidates, time_limit_seconds=5.0)


def test_stateless_extractor_still_rejects_periodic_state() -> None:
    module = _state_module(step_before_output=False)

    with pytest.raises(MappingProblemError, match="without periodic state"):
        build_stateless_level_mapping_problem(module, output_phases=(7,))


def test_periodic_extractors_reject_nonpositive_period() -> None:
    module = _state_module(step_before_output=False)

    with pytest.raises(MappingProblemError, match="positive integer"):
        build_periodic_level_mapping_problem(module, period=0, output_phases=(0,))
    with pytest.raises(MappingProblemError, match="positive integer"):
        build_periodic_state_mapping_problem(module, period=0, output_phases=(0,))
