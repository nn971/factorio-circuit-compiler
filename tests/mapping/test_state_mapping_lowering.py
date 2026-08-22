import pytest

from factorio_circuit import Circuit, SamplingPolicy, SignalId
from factorio_circuit.ir.abstract_physical import ConstantCombinator
from factorio_circuit.lowering.frontend_to_ir import lower_frontend
from factorio_circuit.mapping import (
    build_periodic_state_mapping_problem,
    lower_periodic_state_mapping_plan,
    ordinary_candidates,
    ordinary_state_candidates,
    solve_periodic_state_bus_mapping_problem,
)

_COUNT_SIGNAL = SignalId("virtual", "signal-C")
_LEFT_SIGNAL = SignalId("virtual", "signal-L")
_RIGHT_SIGNAL = SignalId("virtual", "signal-R")


def _freeze_case():
    circuit = Circuit("mapped_freeze_lowering")
    enable = circuit.input("enable")
    memory = circuit.freeze("memory")
    memory.set(circuit.constant_signals({_COUNT_SIGNAL: 1}), when=enable != 0)
    circuit.step(1)
    circuit.output("memory", memory.sample())
    module = lower_frontend(circuit)
    problem = build_periodic_state_mapping_problem(
        module,
        period=8,
        output_phases=(15,),
        sampling_policy=SamplingPolicy.ALAP,
    )
    return module, problem


def _accumulator_case():
    circuit = Circuit("mapped_accumulator_lowering")
    enable = circuit.input("enable")
    reset = circuit.input("reset")
    count = circuit.accumulator("count")
    count.add(circuit.constant_signals({_COUNT_SIGNAL: 1}), when=enable != 0)
    count.clear(reset != 0)
    circuit.step(1)
    circuit.output("count", count.sample())
    module = lower_frontend(circuit)
    problem = build_periodic_state_mapping_problem(
        module,
        period=8,
        output_phases=(15,),
        sampling_policy=SamplingPolicy.ALAP,
    )
    return module, problem


def _two_freeze_bus_case():
    circuit = Circuit("mapped_state_bus_lowering")
    left = circuit.input("left")
    right = circuit.input("right")
    left_memory = circuit.freeze("left_memory")
    right_memory = circuit.freeze("right_memory")
    left_memory.set(circuit.constant_signals({_LEFT_SIGNAL: 1}), when=left != 0)
    right_memory.set(circuit.constant_signals({_RIGHT_SIGNAL: 1}), when=right != 0)
    circuit.step(1)
    circuit.output("left_memory", left_memory.sample())
    circuit.output("right_memory", right_memory.sample())
    module = lower_frontend(circuit)
    problem = build_periodic_state_mapping_problem(
        module,
        period=8,
        output_phases=(15, 15),
        sampling_policy=SamplingPolicy.BEGINNING_OF_STEP,
    )
    return module, problem


def _solve_and_lower(module, problem, *, max_delay_buses: int = 0, capacity: int = 256):
    pytest.importorskip("ortools.sat.python.cp_model")
    candidates = ordinary_candidates(problem)
    state_candidates = ordinary_state_candidates(problem)
    result = solve_periodic_state_bus_mapping_problem(
        problem,
        candidates=candidates,
        state_candidates=state_candidates,
        max_delay_buses=max_delay_buses,
        delay_bus_capacity=capacity,
        time_limit_seconds=5.0,
    )
    lowered = lower_periodic_state_mapping_plan(
        module,
        problem,
        candidates,
        state_candidates,
        result.plan,
    )
    return result, lowered


def test_clocked_freeze_plan_lowers_cost_exactly_after_fixed_vector_constant() -> None:
    module, problem = _freeze_case()

    result, lowered = _solve_and_lower(module, problem)

    assert result.proven_optimal
    assert result.plan.entity_cost == 8
    assert result.plan.transport_cost == 0
    assert lowered.fixed_source_entities == 1
    assert lowered.candidate_internal_entities == 0
    assert lowered.emitted_combinators == 9
    assert lowered.accounted_cost == 9
    assert lowered.cost_exact_after_known_surcharges
    descriptions = {entity.description or "" for entity in lowered.circuit.entities}
    assert "mapped periodic commit: +1" in descriptions
    assert "mapped periodic commit: modulo-8 counter" in descriptions
    assert "mapped periodic commit: ready after first safe boundary" in descriptions
    assert "Mapped FreezeReg memory: pass" in descriptions
    assert "Mapped FreezeReg memory: hold" in descriptions


def test_clocked_accumulator_plan_lowers_cost_exactly_after_fixed_vector_constant() -> None:
    module, problem = _accumulator_case()

    result, lowered = _solve_and_lower(module, problem)

    assert result.proven_optimal
    assert result.plan.entity_cost == 9
    assert result.plan.transport_cost == 0
    assert lowered.fixed_source_entities == 1
    assert lowered.candidate_internal_entities == 0
    assert lowered.emitted_combinators == 10
    assert lowered.accounted_cost == 10
    assert lowered.cost_exact_after_known_surcharges
    descriptions = {entity.description or "" for entity in lowered.circuit.entities}
    assert "Mapped AccumulatorReg count: add active" in descriptions
    assert "Mapped AccumulatorReg count: retain" in descriptions
    assert "Mapped AccumulatorReg count: gated add" in descriptions
    assert "Mapped AccumulatorReg count: vector memory" in descriptions


def test_stateful_delay_bus_lowers_to_the_same_charged_topology() -> None:
    module, problem = _two_freeze_bus_case()

    result, lowered = _solve_and_lower(module, problem, max_delay_buses=1, capacity=2)

    assert result.proven_optimal
    assert result.plan.entity_cost == 13
    assert result.plan.transport_cost == 7
    assert len(result.plan.delay_buses) == 1
    assert lowered.fixed_source_entities == 2
    assert lowered.candidate_internal_entities == 0
    assert lowered.emitted_combinators == 22
    assert lowered.accounted_cost == 22
    assert lowered.cost_exact_after_known_surcharges

    implementation_constants = [
        entity
        for entity in lowered.circuit.entities
        if isinstance(entity, ConstantCombinator) and not entity.annotation_only
    ]
    # Two vector data constants plus the shared periodic +1 clock source.
    assert len(implementation_constants) == 3
    descriptions = [entity.description or "" for entity in lowered.circuit.entities]
    assert descriptions.count("mapped state delay bus ingress") == 2
    assert descriptions.count("mapped state shared delay bus 0") == 3
