import pytest

from factorio_circuit import Circuit, SamplingPolicy, SignalId
from factorio_circuit.ir.abstract_physical import DeciderCombinator
from factorio_circuit.lowering.frontend_to_ir import lower_frontend
from factorio_circuit.mapping import (
    ImplementationKind,
    ImplementationRecipe,
    add_decider_condition_cover_candidates,
    build_periodic_state_mapping_problem,
    find_decider_condition_covers,
    lower_periodic_state_mapping_plan,
    ordinary_candidates,
    ordinary_state_candidates,
    solve_periodic_state_bus_mapping_problem,
)

_MEMORY_SIGNAL = SignalId("virtual", "signal-M")


def _covered_state_case(*, boolean_op: str = "|"):
    circuit = Circuit(f"decider_cover_{boolean_op}")
    a = circuit.input("a")
    b = circuit.input("b")
    c = circuit.input("c")
    d = circuit.input("d")
    leaves = (a < 0, b >= 2, c == 7, d != -3)
    if boolean_op == "|":
        condition = (leaves[0] | leaves[1]) | (leaves[2] | leaves[3])
    else:
        condition = (leaves[0] & leaves[1]) & (leaves[2] & leaves[3])

    memory = circuit.freeze("memory")
    memory.set(circuit.constant_signals({_MEMORY_SIGNAL: 1}), when=condition)
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


def test_find_maximal_four_leaf_or_cover() -> None:
    _module, problem = _covered_state_case()

    covers = find_decider_condition_covers(problem)

    assert len(covers) == 1
    cover = covers[0]
    assert cover.boolean_op == "|"
    assert len(cover.comparisons) == 4
    assert len(cover.operation_ids) == 7


def test_cover_candidate_set_prices_one_decider_and_six_phantoms() -> None:
    _module, problem = _covered_state_case()
    candidates = add_decider_condition_cover_candidates(problem, ordinary_candidates(problem))
    cover = find_decider_condition_covers(problem)[0]
    by_operation = {
        operation_id: next(item for item in candidates if item.operation == operation_id)
        for operation_id in cover.operation_ids
    }

    root = by_operation[cover.root_operation]
    assert root.recipe is ImplementationRecipe.DECIDER_CONDITION_COVER
    assert root.entity_cost == 1
    assert root.input_phase_offsets == (-1, -1)

    covered = [
        candidate
        for operation_id, candidate in by_operation.items()
        if operation_id != cover.root_operation
    ]
    assert len(covered) == 6
    assert all(item.recipe is ImplementationRecipe.COVERED_BY_DECIDER for item in covered)
    assert all(item.kind is ImplementationKind.COVERED for item in covered)
    assert all(item.entity_cost == 0 for item in covered)
    assert all(set(item.input_phase_offsets) == {0} for item in covered)


def test_periodic_lowerer_emits_one_four_condition_or_decider() -> None:
    pytest.importorskip("ortools.sat.python.cp_model")
    module, problem = _covered_state_case()
    candidates = add_decider_condition_cover_candidates(problem, ordinary_candidates(problem))
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

    assert solve.proven_optimal
    assert lowered.cost_exact_after_known_surcharges
    candidate_by_id = {item.id: item for item in candidates}
    selected_recipes = [
        candidate_by_id[item.candidate].recipe for item in solve.plan.realizations
    ]
    assert selected_recipes.count(ImplementationRecipe.DECIDER_CONDITION_COVER) == 1
    assert selected_recipes.count(ImplementationRecipe.COVERED_BY_DECIDER) == 6

    cover_entities = [
        entity
        for entity in lowered.circuit.entities
        if isinstance(entity, DeciderCombinator)
        and (entity.description or "").startswith("Mapped Decider cover")
    ]
    assert len(cover_entities) == 1
    entity = cover_entities[0]
    assert len(entity.additional_conditions) == 3
    assert all(condition.compare_type == "or" for condition in entity.additional_conditions)


def test_periodic_lowerer_emits_and_conditions_for_and_cover() -> None:
    pytest.importorskip("ortools.sat.python.cp_model")
    module, problem = _covered_state_case(boolean_op="&")
    candidates = add_decider_condition_cover_candidates(problem, ordinary_candidates(problem))
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

    entity = next(
        entity
        for entity in lowered.circuit.entities
        if isinstance(entity, DeciderCombinator)
        and (entity.description or "").startswith("Mapped Decider cover")
    )
    assert all(condition.compare_type == "and" for condition in entity.additional_conditions)
