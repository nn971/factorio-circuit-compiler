import pytest

from factorio_circuit import Circuit, SamplingPolicy, SignalId
from factorio_circuit.blueprint.routing import DEFAULT_SAFE_WIRE_SPAN
from factorio_circuit.ir.abstract_physical import DeciderCombinator as AbstractDeciderCombinator
from factorio_circuit.ir.physical import DeciderCombinator as PhysicalDeciderCombinator
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
from factorio_circuit.synthesis.open_vector import synthesize_vector_layout
from factorio_circuit.synthesis.safe_crossbar import safe_crossbar_options

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


def _solve_and_lower_cover(*, boolean_op: str = "|"):
    pytest.importorskip("ortools.sat.python.cp_model")
    module, problem = _covered_state_case(boolean_op=boolean_op)
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
    return candidates, solve, lowered


def test_find_maximal_four_leaf_or_cover() -> None:
    _module, problem = _covered_state_case()

    covers = find_decider_condition_covers(problem)

    assert len(covers) == 1
    cover = covers[0]
    assert cover.boolean_op == "|"
    assert len(cover.comparisons) == 4
    assert len(cover.operation_ids) == 7


def test_cover_candidates_keep_ordinary_fallbacks_and_form_one_group() -> None:
    _module, problem = _covered_state_case()
    candidates = add_decider_condition_cover_candidates(problem, ordinary_candidates(problem))
    cover = find_decider_condition_covers(problem)[0]

    for operation_id in cover.operation_ids:
        alternatives = [item for item in candidates if item.operation == operation_id]
        assert any(item.recipe is ImplementationRecipe.ORDINARY for item in alternatives)
        specialized = [
            item
            for item in alternatives
            if item.recipe
            in {
                ImplementationRecipe.DECIDER_CONDITION_COVER,
                ImplementationRecipe.COVERED_BY_DECIDER,
            }
        ]
        assert len(specialized) == 1
        assert specialized[0].coupling_group == cover.root_operation

    root = next(
        item
        for item in candidates
        if item.operation == cover.root_operation
        and item.recipe is ImplementationRecipe.DECIDER_CONDITION_COVER
    )
    assert root.entity_cost == 1
    assert root.input_phase_offsets == (-1, -1)

    covered = [
        item
        for item in candidates
        if item.operation in cover.internal_operation_ids
        and item.recipe is ImplementationRecipe.COVERED_BY_DECIDER
    ]
    assert len(covered) == 6
    assert all(item.kind is ImplementationKind.COVERED for item in covered)
    assert all(item.entity_cost == 0 for item in covered)
    assert all(set(item.input_phase_offsets) == {0} for item in covered)


def test_periodic_lowerer_emits_one_four_condition_or_decider() -> None:
    candidates, solve, lowered = _solve_and_lower_cover()

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
        if isinstance(entity, AbstractDeciderCombinator)
        and (entity.description or "").startswith("Mapped Decider cover")
    ]
    assert len(cover_entities) == 1
    entity = cover_entities[0]
    assert len(entity.additional_conditions) == 3
    assert all(condition.compare_type == "or" for condition in entity.additional_conditions)


def test_periodic_lowerer_emits_and_conditions_for_and_cover() -> None:
    _candidates, _solve, lowered = _solve_and_lower_cover(boolean_op="&")

    entity = next(
        entity
        for entity in lowered.circuit.entities
        if isinstance(entity, AbstractDeciderCombinator)
        and (entity.description or "").startswith("Mapped Decider cover")
    )
    assert all(condition.compare_type == "and" for condition in entity.additional_conditions)


def test_four_condition_cover_survives_physical_synthesis() -> None:
    _candidates, _solve, lowered = _solve_and_lower_cover()

    layout = synthesize_vector_layout(
        lowered.circuit,
        safe_wire_span=DEFAULT_SAFE_WIRE_SPAN,
        placement=safe_crossbar_options(),
    )

    entity = next(
        entity
        for entity in layout.circuit.entities
        if isinstance(entity, PhysicalDeciderCombinator)
        and (entity.description or "").startswith("Mapped Decider cover")
    )
    assert len(entity.additional_conditions) == 3
