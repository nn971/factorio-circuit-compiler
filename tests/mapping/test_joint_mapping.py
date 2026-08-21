import pytest

from factorio_circuit.ir.semantic import BinaryOp, Constant, PayloadShape, Select
from factorio_circuit.mapping import (
    MappingOperation,
    MappingProblem,
    MappingSink,
    MappingSource,
    MappingSourceMode,
    ordinary_candidate,
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
            MappingSource(1, "left", PayloadShape.SCALAR, source_mode, 0, end),
            MappingSource(2, "right", PayloadShape.SCALAR, source_mode, 0, end),
        ),
        operations=(_add_operation(),),
        sinks=tuple(sinks),
    )


def test_ordinary_candidate_owns_factorio_latency() -> None:
    candidate = ordinary_candidate(_add_operation(), candidate_id=7)

    assert candidate.operation == 3
    assert candidate.input_phase_offsets == (-1, -1)
    assert candidate.entity_cost == 1


def test_select_candidate_owns_asymmetric_input_latency() -> None:
    semantic = Select(
        Constant(1),
        Constant(7),
        Constant(9),
        name="mux",
    )
    operation = MappingOperation(4, "mux", PayloadShape.SCALAR, (1, 2, 3), semantic)

    candidate = ordinary_candidate(operation, candidate_id=8)

    assert candidate.input_phase_offsets == (-2, -3, -3)


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
    assert {(item.producer, item.start_phase, item.end_phase) for item in result.plan.exact_lifetimes} == {
        (1, 0, 5),
        (2, 0, 5),
        (3, 6, 10),
    }
