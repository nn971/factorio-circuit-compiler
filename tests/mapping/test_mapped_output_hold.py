import pytest

from factorio_circuit import Circuit, SamplingPolicy, SignalId
from factorio_circuit.lowering.frontend_to_ir import lower_frontend
from factorio_circuit.mapping import (
    build_periodic_state_mapping_problem,
    lower_periodic_state_mapping_plan,
    ordinary_candidates,
    ordinary_state_candidates,
    solve_periodic_state_bus_mapping_problem,
)

_PIXEL = SignalId("virtual", "signal-P")


def test_mapped_framebuffer_is_captured_once_per_period() -> None:
    pytest.importorskip("ortools.sat.python.cp_model")

    circuit = Circuit("mapped_framebuffer_hold")
    enable = circuit.input("enable")
    memory = circuit.freeze("memory")
    memory.set(circuit.constant_signals({_PIXEL: 1}), when=enable != 0)
    circuit.step(1)
    circuit.output("framebuffer", memory.sample())

    module = lower_frontend(circuit)
    problem = build_periodic_state_mapping_problem(
        module,
        period=8,
        output_phases=(15,),
        sampling_policy=SamplingPolicy.ALAP,
    )
    candidates = ordinary_candidates(problem)
    state_candidates = ordinary_state_candidates(problem)
    solved = solve_periodic_state_bus_mapping_problem(
        problem,
        candidates=candidates,
        state_candidates=state_candidates,
        max_delay_buses=0,
        time_limit_seconds=5.0,
    )
    lowered = lower_periodic_state_mapping_plan(
        module,
        problem,
        candidates,
        state_candidates,
        solved.plan,
    )

    assert solved.proven_optimal
    assert lowered.output_materialization_entities == 2
    assert lowered.fixed_source_entities == 1
    assert lowered.candidate_internal_entities == 0
    assert lowered.emitted_combinators == solved.plan.total_cost + 3
    assert lowered.accounted_cost == lowered.emitted_combinators
    assert lowered.cost_exact_after_known_surcharges

    descriptions = [entity.description or "" for entity in lowered.circuit.entities]
    assert descriptions.count("Mapped Level HOLD: capture coherent framebuffer") == 1
    assert descriptions.count("Mapped Level HOLD: retain framebuffer between boundaries") == 1

    framebuffer = next(port for port in lowered.circuit.outputs if port.name == "framebuffer")
    assert framebuffer.phase == 16
