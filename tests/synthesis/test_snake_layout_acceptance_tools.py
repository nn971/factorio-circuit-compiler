from benchmarks.layout_optimizer_topology_corpus import _near_optimal_packed_case
from benchmarks.snake.layout_acceptance import application_layout_metrics


def test_application_layout_metrics_reports_exact_full_occupancy() -> None:
    case = _near_optimal_packed_case()

    metrics = application_layout_metrics(case.problem.layout, problem=case.problem)

    assert metrics.implementation_combinators == 48
    assert metrics.relay_combinators == 0
    assert metrics.entity_footprint_area == 48.0
    assert metrics.bounding_box_area == 48.0
    assert metrics.occupancy == 1.0
    assert metrics.implementation_per_relay is None
    assert metrics.known_redundant_relays == 0
