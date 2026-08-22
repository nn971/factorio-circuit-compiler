import pytest

from factorio_circuit import Circuit, SamplingPolicy, SignalId
from factorio_circuit.ir.abstract_physical import ArithmeticCombinator
from factorio_circuit.ir.semantic import Constant, PayloadShape, Select
from factorio_circuit.lowering.frontend_to_ir import lower_frontend
from factorio_circuit.mapping import (
    ImplementationRecipe,
    MappingOperation,
    add_select_constant_candidates,
    build_periodic_state_mapping_problem,
    lower_periodic_state_mapping_plan,
    ordinary_candidates,
    ordinary_state_candidates,
    select_constant_candidate,
    solve_periodic_state_bus_mapping_problem,
)

_COUNT_SIGNAL = SignalId("virtual", "signal-C")


def _select_operation(*, false_value: int) -> MappingOperation:
    semantic = Select(
        Constant(1),
        Constant(7),
        Constant(false_value),
        name="choice",
    )
    return MappingOperation(4, "choice", PayloadShape.SCALAR, (1, 2, 3), semantic)


def test_zero_false_constant_select_candidate_is_one_tick_one_entity() -> None:
    candidate = select_constant_candidate(_select_operation(false_value=0), candidate_id=9)

    assert candidate.input_phase_offsets == (-1, 0, 0)
    assert candidate.entity_cost == 1
    assert candidate.recipe is ImplementationRecipe.SELECT_CONSTANT_ZERO_FALSE


def test_nonzero_false_constant_select_candidate_folds_delta_to_two_entities() -> None:
    candidate = select_constant_candidate(_select_operation(false_value=3), candidate_id=9)

    assert candidate.input_phase_offsets == (-2, 0, 0)
    assert candidate.entity_cost == 2
    assert candidate.recipe is ImplementationRecipe.SELECT_CONSTANT_FOLDED


def _stateful_select_case(*, false_value: int):
    circuit = Circuit(f"mapped_select_{false_value}")
    enable = circuit.input("enable")
    active = enable != 0
    choice = active.select(7, false_value)

    memory = circuit.freeze("memory")
    memory.set(circuit.constant_signals({_COUNT_SIGNAL: 1}) * choice, when=active)
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


def _solve_and_lower_constant_select(*, false_value: int):
    pytest.importorskip("ortools.sat.python.cp_model")
    module, problem = _stateful_select_case(false_value=false_value)
    candidates = add_select_constant_candidates(problem, ordinary_candidates(problem))
    state_candidates = ordinary_state_candidates(problem)
    solve = solve_periodic_state_bus_mapping_problem(
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
        solve.plan,
    )
    select_operation = next(item for item in problem.operations if isinstance(item.semantic, Select))
    realization = solve.plan.realization_for(select_operation.id)
    selected = next(item for item in candidates if item.id == realization.candidate)
    return solve, lowered, selected


def test_periodic_mapper_selects_and_lowers_zero_false_recipe_exactly() -> None:
    solve, lowered, selected = _solve_and_lower_constant_select(false_value=0)

    assert solve.proven_optimal
    assert selected.recipe is ImplementationRecipe.SELECT_CONSTANT_ZERO_FALSE
    assert selected.entity_cost == 1
    assert lowered.cost_exact_after_known_surcharges

    select_entities = [
        entity
        for entity in lowered.circuit.entities
        if isinstance(entity, ArithmeticCombinator)
        and (entity.description or "").startswith("Mapped Select")
    ]
    assert len(select_entities) == 1
    assert "constant gate" in (select_entities[0].description or "")
    assert not (
        select_entities[0].left.constant is not None
        and select_entities[0].right.constant is not None
    )


def test_periodic_mapper_selects_and_lowers_folded_constant_recipe_exactly() -> None:
    solve, lowered, selected = _solve_and_lower_constant_select(false_value=3)

    assert solve.proven_optimal
    assert selected.recipe is ImplementationRecipe.SELECT_CONSTANT_FOLDED
    assert selected.entity_cost == 2
    assert lowered.cost_exact_after_known_surcharges

    select_entities = [
        entity
        for entity in lowered.circuit.entities
        if isinstance(entity, ArithmeticCombinator)
        and (entity.description or "").startswith("Mapped Select")
    ]
    assert len(select_entities) == 2
    descriptions = {entity.description or "" for entity in select_entities}
    assert any("folded constant delta 4" in item for item in descriptions)
    assert any("add false constant 3" in item for item in descriptions)
    assert all(
        not (entity.left.constant is not None and entity.right.constant is not None)
        for entity in select_entities
    )
