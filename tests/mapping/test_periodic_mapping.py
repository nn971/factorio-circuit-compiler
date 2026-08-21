import pytest

from factorio_circuit import Circuit, SamplingPolicy, SignalId
from factorio_circuit.ir.state import VectorRegisterRead
from factorio_circuit.lowering.frontend_to_ir import lower_frontend
from factorio_circuit.mapping import (
    MappingProblemError,
    MappingSourceMode,
    build_periodic_level_mapping_problem,
    build_stateless_level_mapping_problem,
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


def test_periodic_extractor_models_current_register_occurrence_as_stable_window() -> None:
    module = _state_module(step_before_output=False)

    problem = build_periodic_level_mapping_problem(
        module,
        period=8,
        output_phases=(7,),
        sampling_policy=SamplingPolicy.ALAP,
    )

    assert len(problem.sources) == 1
    source = problem.sources[0]
    assert isinstance(source.semantic, VectorRegisterRead)
    assert source.semantic.offset == 0
    assert source.mode is MappingSourceMode.STABLE
    assert (source.start_phase, source.end_phase_exclusive) == (0, 8)
    assert problem.sinks[0].phase == 7
    # State-transition value/control cones exist in the module but are deliberately not pulled into
    # the first occurrence-output mapping problem merely because the transition exists.
    assert problem.operations == ()


def test_periodic_extractor_reindexes_next_register_occurrence_without_state_timing_plan() -> None:
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


def test_periodic_register_token_is_free_until_boundary_but_not_through_it() -> None:
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


def test_stateless_extractor_still_rejects_periodic_state() -> None:
    module = _state_module(step_before_output=False)

    with pytest.raises(MappingProblemError, match="without periodic state"):
        build_stateless_level_mapping_problem(module, output_phases=(7,))


def test_periodic_extractor_rejects_nonpositive_period() -> None:
    module = _state_module(step_before_output=False)

    with pytest.raises(MappingProblemError, match="positive integer"):
        build_periodic_level_mapping_problem(module, period=0, output_phases=(0,))
