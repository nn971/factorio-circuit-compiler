import pytest

from benchmarks.layout_optimizer_topology_corpus import (
    _clustered_sparse_cut_case,
    _large_sparse_case,
    _near_optimal_packed_case,
    _red_green_mesh_case,
)
from factorio_circuit.synthesis.layout_optimizer import (
    optimize_physical_layout,
    validate_physical_layout,
)
from factorio_circuit.synthesis.placement import PlacementOptions


@pytest.mark.parametrize(
    "case_factory",
    [_clustered_sparse_cut_case, _red_green_mesh_case, _near_optimal_packed_case],
)
def test_topology_corpus_fixture_is_valid_and_zero_budget_is_exact_pass_through(
    case_factory,
) -> None:
    case = case_factory()
    validate_physical_layout(case.problem)

    result = optimize_physical_layout(
        case.problem,
        options=PlacementOptions(
            anchor_io=False,
            reserve_corridors=False,
            iterations=0,
            restarts=1,
        ),
    )

    assert result.layout == case.problem.layout
    assert result.before == result.after
    assert result.proposal_budget == 0


@pytest.mark.benchmark
def test_large_sparse_corpus_fixture_is_valid() -> None:
    case = _large_sparse_case()
    validate_physical_layout(case.problem)
