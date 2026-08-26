import pytest

from benchmarks.layout_optimizer_corpus import _fixed_endpoint_span_case, _shared_bus_case
from factorio_circuit.synthesis.layout_optimizer import (
    optimize_physical_layout,
    validate_physical_layout,
)
from factorio_circuit.synthesis.placement import PlacementOptions


@pytest.mark.parametrize("case_factory", [_shared_bus_case, _fixed_endpoint_span_case])
def test_layout_corpus_fixture_is_valid_and_zero_budget_is_exact_pass_through(case_factory) -> None:
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
