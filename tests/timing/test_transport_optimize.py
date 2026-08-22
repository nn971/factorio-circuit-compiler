from importlib.util import find_spec

import pytest

from factorio_circuit.analysis import (
    ExactTransportDemand,
    TemporalAlignmentAnalysis,
    optimize_exact_transports,
)
from factorio_circuit.ir.semantic import PayloadShape


def _analysis(*transports: ExactTransportDemand) -> TemporalAlignmentAnalysis:
    return TemporalAlignmentAnalysis(availabilities=(), uses=(), transports=tuple(transports))


def _scalar(
    producer: int,
    start: int,
    end: int,
    *,
    taps: tuple[int, ...] | None = None,
) -> ExactTransportDemand:
    phases = taps or (end,)
    return ExactTransportDemand(
        producer=producer,
        label=f"p{producer}",
        shape=PayloadShape.SCALAR,
        start_phase=start,
        end_phase=end,
        consumers=(producer + 100,),
        tap_phases=phases,
    )


def test_no_exact_transport_means_zero_bus_cost_without_solver() -> None:
    result = optimize_exact_transports(_analysis())

    assert result.proven_optimal
    assert result.buses == ()
    assert result.private_transports == ()
    assert result.objective_combinators == 0


def test_short_scalar_transport_stays_private_without_solver() -> None:
    transport = _scalar(1, 4, 6)
    result = optimize_exact_transports(_analysis(transport))

    assert result.buses == ()
    assert result.private_transports == (transport,)
    assert result.private_scalar_combinators == 2
    assert result.objective_combinators == 2


@pytest.mark.skipif(find_spec("ortools") is None, reason="OR-Tools is an optional dependency")
def test_two_matching_long_transports_share_one_middle_stage() -> None:
    first = _scalar(1, 0, 3)
    second = _scalar(2, 0, 3)

    result = optimize_exact_transports(
        _analysis(first, second),
        time_limit_seconds=5,
        workers=1,
    )

    # Private chains cost 3 + 3 = 6.  The isolated bus costs one shared middle stage plus one
    # ingress and one egress for each lane: 1 + 2 + 2 = 5.
    assert result.proven_optimal
    assert result.objective_combinators == 5
    assert result.bus_middle_stages == 1
    assert result.bus_interface_combinators == 4
    assert result.private_scalar_combinators == 0
    assert len(result.buses) == 1
    assert len(result.buses[0].lanes) == 2
    lane_ids = {lane.lane_id for lane in result.buses[0].lanes}
    assert len(lane_ids) == 2


@pytest.mark.skipif(find_spec("ortools") is None, reason="OR-Tools is an optional dependency")
def test_far_apart_transports_do_not_pay_for_empty_bus_span() -> None:
    first = _scalar(1, 0, 3)
    second = _scalar(2, 5, 8)

    result = optimize_exact_transports(
        _analysis(first, second),
        time_limit_seconds=5,
        workers=1,
    )

    assert result.proven_optimal
    assert result.buses == ()
    assert result.private_scalar_combinators == 6
    assert result.objective_combinators == 6


@pytest.mark.skipif(find_spec("ortools") is None, reason="OR-Tools is an optional dependency")
def test_bus_capacity_counts_unique_abstract_lanes() -> None:
    first = _scalar(1, 0, 3)
    second = _scalar(2, 0, 3)

    result = optimize_exact_transports(
        _analysis(first, second),
        bus_capacity=1,
        time_limit_seconds=5,
        workers=1,
    )

    assert result.buses == ()
    assert result.objective_combinators == 6
