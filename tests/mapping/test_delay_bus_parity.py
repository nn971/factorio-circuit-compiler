import pytest

from factorio_circuit.analysis.temporal_alignment import (
    ExactTransportDemand,
    TemporalAlignmentAnalysis,
)
from factorio_circuit.analysis.transport_optimize import optimize_exact_transports
from factorio_circuit.ir.semantic import Constant, PayloadShape
from factorio_circuit.mapping import (
    MappingProblem,
    MappingSink,
    MappingSource,
    MappingSourceMode,
    solve_mapping_problem,
)


def _fixed_problem(
    lifetimes: tuple[tuple[int, int, tuple[int, ...]], ...],
) -> MappingProblem:
    """Build a mapping problem whose exact lifetimes are fixed by source/sink phases."""

    sources = []
    sinks = []
    next_sink = 10_000
    horizon = 0
    for producer, start, taps in lifetimes:
        sources.append(
            MappingSource(
                producer,
                f"source-{producer}",
                PayloadShape.SCALAR,
                MappingSourceMode.EXACT,
                Constant(producer),
                start_phase=start,
                end_phase_exclusive=start + 1,
            )
        )
        for phase in taps:
            sinks.append(MappingSink(next_sink, f"sink-{next_sink}", producer, phase))
            next_sink += 1
            horizon = max(horizon, phase)
    return MappingProblem(horizon, tuple(sources), (), tuple(sinks))


def _fixed_alignment(
    lifetimes: tuple[tuple[int, int, tuple[int, ...]], ...],
) -> TemporalAlignmentAnalysis:
    """Project the same fixed lifetimes into the established transport optimizer input."""

    transports = tuple(
        ExactTransportDemand(
            producer=producer,
            label=f"source-{producer}",
            shape=PayloadShape.SCALAR,
            start_phase=start,
            end_phase=max(taps),
            consumers=tuple(range(index * 100, index * 100 + len(taps))),
            # The established optimizer represents taps as physical tap phases, so equal-phase
            # semantic consumers are intentionally coalesced here.
            tap_phases=tuple(sorted(set(taps))),
        )
        for index, (producer, start, taps) in enumerate(lifetimes, start=1)
    )
    return TemporalAlignmentAnalysis((), (), transports)


def test_joint_bus_matches_fixed_optimizer_on_fixed_unique_taps() -> None:
    pytest.importorskip("ortools.sat.python.cp_model")
    lifetimes = (
        (1, 0, (6,)),
        (2, 0, (6,)),
        (3, 0, (2,)),
    )

    joint = solve_mapping_problem(
        _fixed_problem(lifetimes),
        max_delay_buses=1,
        delay_bus_capacity=8,
        time_limit_seconds=5.0,
    )
    fixed = optimize_exact_transports(
        _fixed_alignment(lifetimes),
        max_buses=1,
        bus_capacity=8,
        time_limit_seconds=5.0,
    )

    assert joint.proven_optimal
    assert fixed.proven_optimal
    assert joint.plan.transport_cost == fixed.objective_combinators == 10
    assert len(joint.plan.delay_buses) == len(fixed.buses) == 1

    joint_bus = joint.plan.delay_buses[0]
    fixed_bus = fixed.buses[0]
    assert (
        (joint_bus.middle_start_phase, joint_bus.middle_end_phase)
        == (
            fixed_bus.start_phase,
            fixed_bus.end_phase,
        )
        == (1, 5)
    )
    assert (
        {lane.producer for lane in joint_bus.lanes}
        == {lane.producer for lane in fixed_bus.lanes}
        == {1, 2}
    )


def test_joint_bus_matches_fixed_optimizer_with_staggered_fixed_lifetimes() -> None:
    pytest.importorskip("ortools.sat.python.cp_model")
    lifetimes = (
        (1, 0, (5, 7)),
        (2, 2, (8,)),
        (3, 1, (6,)),
    )

    joint = solve_mapping_problem(
        _fixed_problem(lifetimes),
        max_delay_buses=1,
        delay_bus_capacity=8,
        time_limit_seconds=5.0,
    )
    fixed = optimize_exact_transports(
        _fixed_alignment(lifetimes),
        max_buses=1,
        bus_capacity=8,
        time_limit_seconds=5.0,
    )

    assert joint.proven_optimal
    assert fixed.proven_optimal
    assert joint.plan.transport_cost == fixed.objective_combinators

    joint_private = {
        lifetime.producer
        for lifetime in joint.plan.exact_lifetimes
        if joint.plan.delay_bus_for(lifetime.producer) is None
    }
    fixed_private = {item.producer for item in fixed.private_transports}
    assert joint_private == fixed_private


def test_equal_phase_multiuse_difference_is_explicit() -> None:
    """Document the one intentional cost-model difference during migration.

    The established fixed-placement optimizer coalesces equal-phase taps. The first joint mapper
    charges one isolated egress per semantic use. Until joint egress coalescing is modeled
    explicitly,
    parity tests must not silently assume those two contracts are identical.
    """

    pytest.importorskip("ortools.sat.python.cp_model")
    lifetimes = (
        (1, 0, (6, 6)),
        (2, 0, (6, 6)),
    )

    joint = solve_mapping_problem(
        _fixed_problem(lifetimes),
        max_delay_buses=1,
        delay_bus_capacity=8,
        time_limit_seconds=5.0,
    )
    fixed = optimize_exact_transports(
        _fixed_alignment(lifetimes),
        max_buses=1,
        bus_capacity=8,
        time_limit_seconds=5.0,
    )

    assert joint.proven_optimal
    assert fixed.proven_optimal
    assert joint.plan.transport_cost == 10
    assert fixed.objective_combinators == 8
