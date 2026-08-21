from factorio_circuit import Circuit, SamplingPolicy, SignalId
from factorio_circuit.analysis import (
    DelayBusLane,
    DelayBusPlan,
    TemporalOptimizationResult,
    build_temporal_hypergraph,
    census_abstract_physical,
)
from factorio_circuit.compiler import lower_to_abstract_physical
from factorio_circuit.lowering.temporal_plan import lower_normalized_vectors_with_temporal_plan

VALUE = SignalId("virtual", "signal-A")


def _two_shared_live_controls() -> Circuit:
    c = Circuit("two_shared_live_controls")
    left = c.input("left")
    right = c.input("right")
    one = c.constant_signals({VALUE: 1})
    memory = c.accumulator("memory")

    # Establish a multicycle state period independently of the external controls.
    deep = memory.sample()
    for _ in range(6):
        deep = deep + one
    memory.add(deep)

    # Each comparison is needed both directly at the state boundary and several stages earlier by a
    # deeper control expression. Under the one-realization model each comparison therefore has a
    # short exact lifetime. The two lifetimes are structurally identical and should share one bus.
    left_shared = left != 0
    right_shared = right != 0
    left_late = left_shared
    right_late = right_shared
    for _ in range(3):
        left_late = left_late + 1
        right_late = right_late + 1

    memory.add(one, when=left_shared)
    memory.add(one, when=left_late)
    memory.add(one, when=right_shared)
    memory.add(one, when=right_late)

    c.step(1)
    c.output("memory", memory.sample())
    return c


def test_temporal_plan_realizes_shared_multilane_scalar_bus() -> None:
    baseline = lower_to_abstract_physical(
        _two_shared_live_controls(),
        optimize=False,
        sampling_policy=SamplingPolicy.ALAP,
    )
    graph = build_temporal_hypergraph(
        baseline.optimized_ir,
        baseline.state_timing,
        sampling_policy=SamplingPolicy.ALAP,
    )
    placement = graph.alap_placement()
    intervals = [
        item
        for item in graph.transport_intervals(placement)
        if item.delay_bus_eligible and item.length > 0
    ]
    assert len(intervals) >= 2

    # Put all phase-specific scalar lifetimes on one deliberately simple test bus. This is not an
    # optimization algorithm; it isolates whether the lowerer can realize a solved bus assignment.
    lanes = tuple(
        DelayBusLane(
            producer=item.producer,
            label=item.label,
            start_phase=item.start_phase,
            end_phase=item.end_phase,
        )
        for item in intervals
    )
    bus = DelayBusPlan(
        index=0,
        start_phase=min(item.start_phase for item in intervals),
        end_phase=max(item.end_phase for item in intervals),
        lanes=lanes,
    )
    optimization = TemporalOptimizationResult(
        status="FEASIBLE",
        placement=placement,
        buses=(bus,),
        bus_stages=bus.stages,
        ordinary_scalar_delays=0,
        vector_delays=0,
        objective_delays=bus.stages,
        best_bound=0,
        wall_time_seconds=0.0,
    )

    planned = lower_normalized_vectors_with_temporal_plan(
        baseline.optimized_ir,
        state_timing=baseline.state_timing,
        sampling_policy=SamplingPolicy.ALAP,
        graph=graph,
        optimization=optimization,
    )

    planned_census = census_abstract_physical(planned)
    roles = dict(planned_census.lowering_roles)

    assert roles.get("phase-delay.scalar-bus", 0) == bus.stages
    assert planned_census.max_signals_per_net >= 2
