import pytest

from factorio_circuit import Circuit, SamplingPolicy
from factorio_circuit.ir.semantic import BinaryOp, Constant, PayloadShape, Select
from factorio_circuit.lowering.frontend_to_ir import lower_frontend
from factorio_circuit.mapping import (
    ImplementationKind,
    MappingOperation,
    MappingProblem,
    MappingSink,
    MappingSource,
    MappingSourceMode,
    add_wire_sum_candidates,
    build_stateless_level_mapping_problem,
    ordinary_candidate,
    ordinary_candidates,
    solve_mapping_problem,
)


def _add_operation() -> MappingOperation:
    semantic = BinaryOp("+", Constant(1), Constant(2), name="sum")
    return MappingOperation(3, "sum", PayloadShape.SCALAR, (1, 2), semantic)


def _problem(
    source_mode: MappingSourceMode,
    *,
    source_sinks: bool = False,
) -> MappingProblem:
    end = 1 if source_mode is MappingSourceMode.EXACT else None
    sinks = [MappingSink(10, "sum-out", 3, 10)]
    if source_sinks:
        sinks.extend(
            (
                MappingSink(11, "left-mid", 1, 5),
                MappingSink(12, "right-mid", 2, 5),
            )
        )
    return MappingProblem(
        horizon=10,
        sources=(
            MappingSource(
                1,
                "left",
                PayloadShape.SCALAR,
                source_mode,
                Constant(1),
                0,
                end,
            ),
            MappingSource(
                2,
                "right",
                PayloadShape.SCALAR,
                source_mode,
                Constant(2),
                0,
                end,
            ),
        ),
        operations=(_add_operation(),),
        sinks=tuple(sinks),
    )


def _wire_sum_problem() -> MappingProblem:
    source_semantics = {
        1: Constant(2),
        2: Constant(3),
        3: Constant(5),
        4: Constant(7),
    }
    sources = tuple(
        MappingSource(
            source_id,
            f"source-{source_id}",
            PayloadShape.SCALAR,
            MappingSourceMode.STABLE,
            source_semantics[source_id],
        )
        for source_id in (1, 2, 3, 4)
    )
    left_semantic = BinaryOp("*", source_semantics[1], source_semantics[2], name="left")
    right_semantic = BinaryOp("*", source_semantics[3], source_semantics[4], name="right")
    sum_semantic = BinaryOp("+", left_semantic, right_semantic, name="sum")
    operations = (
        MappingOperation(5, "left", PayloadShape.SCALAR, (1, 2), left_semantic),
        MappingOperation(6, "right", PayloadShape.SCALAR, (3, 4), right_semantic),
        MappingOperation(7, "sum", PayloadShape.SCALAR, (5, 6), sum_semantic),
    )
    return MappingProblem(
        horizon=10,
        sources=sources,
        operations=operations,
        sinks=(MappingSink(10, "out", 7, 10),),
    )


def test_ordinary_candidate_owns_factorio_latency() -> None:
    candidate = ordinary_candidate(_add_operation(), candidate_id=7)

    assert candidate.operation == 3
    assert candidate.input_phase_offsets == (-1, -1)
    assert candidate.entity_cost == 1


def test_select_candidate_owns_asymmetric_input_latency_and_area() -> None:
    semantic = Select(
        Constant(1),
        Constant(7),
        Constant(9),
        name="mux",
    )
    operation = MappingOperation(4, "mux", PayloadShape.SCALAR, (1, 2, 3), semantic)

    candidate = ordinary_candidate(operation, candidate_id=8)

    assert candidate.input_phase_offsets == (-2, -3, -3)
    assert candidate.entity_cost == 3


def test_stateless_extractor_keeps_target_latency_out_of_problem() -> None:
    circuit = Circuit("mapping_extract")
    left = circuit.input("left")
    right = circuit.input("right")
    circuit.output("sum", left + right)
    module = lower_frontend(circuit)

    problem = build_stateless_level_mapping_problem(
        module,
        output_phases=(10,),
        sampling_policy=SamplingPolicy.ALAP,
    )

    assert problem.horizon == 10
    assert len(problem.sources) == 2
    assert all(source.mode is MappingSourceMode.OBSERVABLE for source in problem.sources)
    assert len(problem.operations) == 1
    assert problem.sinks[0].phase == 10
    candidate = ordinary_candidates(problem)[0]
    assert candidate.input_phase_offsets == (-1, -1)


def test_joint_mapper_can_choose_asap_when_exact_inputs_dominate() -> None:
    pytest.importorskip("ortools.sat.python.cp_model")

    result = solve_mapping_problem(_problem(MappingSourceMode.EXACT), time_limit_seconds=5.0)

    assert result.proven_optimal
    realization = result.plan.realization_for(3)
    assert realization.output_phase == 1
    assert result.plan.entity_cost == 1
    assert result.plan.transport_cost == 9


def test_joint_mapper_can_choose_alap_when_inputs_are_stable() -> None:
    pytest.importorskip("ortools.sat.python.cp_model")

    result = solve_mapping_problem(_problem(MappingSourceMode.STABLE), time_limit_seconds=5.0)

    assert result.proven_optimal
    realization = result.plan.realization_for(3)
    assert realization.output_phase == 10
    assert result.plan.entity_cost == 1
    assert result.plan.transport_cost == 0


def test_joint_mapper_finds_interior_phase_from_shared_exact_lifetimes() -> None:
    pytest.importorskip("ortools.sat.python.cp_model")

    result = solve_mapping_problem(
        _problem(MappingSourceMode.EXACT, source_sinks=True),
        time_limit_seconds=5.0,
    )

    assert result.proven_optimal
    realization = result.plan.realization_for(3)
    assert realization.output_phase == 6
    assert result.plan.entity_cost == 1
    assert result.plan.transport_cost == 14
    lifetimes = {
        (item.producer, item.start_phase, item.end_phase) for item in result.plan.exact_lifetimes
    }
    assert lifetimes == {
        (1, 0, 5),
        (2, 0, 5),
        (3, 6, 10),
    }


def test_wire_sum_candidate_changes_timing_inside_same_solve() -> None:
    pytest.importorskip("ortools.sat.python.cp_model")
    problem = _wire_sum_problem()

    ordinary = solve_mapping_problem(problem, time_limit_seconds=5.0)
    candidates = add_wire_sum_candidates(problem, ordinary_candidates(problem))
    fused = solve_mapping_problem(
        problem,
        candidates=candidates,
        time_limit_seconds=5.0,
    )

    assert ordinary.proven_optimal
    assert fused.proven_optimal
    assert ordinary.plan.realization_for(5).output_phase == 9
    assert ordinary.plan.realization_for(6).output_phase == 9
    assert ordinary.plan.realization_for(7).output_phase == 10

    sum_realization = fused.plan.realization_for(7)
    selected = next(item for item in candidates if item.id == sum_realization.candidate)
    assert selected.kind is ImplementationKind.WIRE_SUM
    assert fused.plan.realization_for(5).output_phase == 10
    assert fused.plan.realization_for(6).output_phase == 10
    assert sum_realization.output_phase == 10
    assert fused.plan.entity_cost == 2
    assert fused.plan.transport_cost == 0
    assert len(fused.plan.wire_sums) == 1
