from factorio_circuit import Circuit, SamplingPolicy, SignalId
from factorio_circuit.analysis import (
    DelayBusLane,
    DelayBusPlan,
    TemporalOptimizationResult,
    build_temporal_hypergraph,
    census_abstract_physical,
)
from factorio_circuit.compiler import lower_to_abstract_physical
from factorio_circuit.ir.semantic import Select
from factorio_circuit.lowering.temporal_plan import lower_normalized_vectors_with_temporal_plan

VALUE = SignalId("virtual", "signal-A")


def _shared_select_control() -> Circuit:
    c = Circuit("temporal_plan_select_alignment")
    enabled = c.input("enabled")
    one = c.constant_signals({VALUE: 1})
    memory = c.accumulator("memory")

    # Establish enough recurrence latency for a nontrivial periodic state cone.
    deep = memory.sample()
    for _ in range(7):
        deep = deep + one
    memory.add(deep)

    condition = enabled != 0
    selected = condition.select(1, 0)
    late = selected
    for _ in range(3):
        late = late + 1

    # The select is needed through both a deeper early path and a direct late state control. That
    # gives its single semantic realization a positive lifetime suitable for a delay-bus lane.
    memory.add(one, when=selected)
    memory.add(one, when=late)

    c.step(1)
    c.output("memory", memory.sample())
    return c


def test_temporal_plan_uses_modeled_select_latency_before_bus_join() -> None:
    baseline = lower_to_abstract_physical(
        _shared_select_control(),
        optimize=False,
        sampling_policy=SamplingPolicy.ALAP,
    )
    graph = build_temporal_hypergraph(
        baseline.optimized_ir,
        baseline.state_timing,
        sampling_policy=SamplingPolicy.ALAP,
    )
    placement = graph.alap_placement()

    select_node = next(
        computation
        for computation in graph.computations
        if isinstance(computation.semantic, Select)
    )
    interval = next(
        item
        for item in graph.transport_intervals(placement)
        if item.producer == select_node.id and item.length > 0
    )
    lane = DelayBusLane(
        producer=interval.producer,
        label=interval.label,
        start_phase=interval.start_phase,
        end_phase=interval.end_phase,
    )
    bus = DelayBusPlan(
        index=0,
        start_phase=lane.start_phase,
        end_phase=lane.end_phase,
        lanes=(lane,),
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
    census = census_abstract_physical(planned)
    roles = dict(census.lowering_roles)

    # Optimized Select nodes must use the same three-stage arithmetic implementation whose
    # asymmetric 3-tick data / 2-tick condition latency the temporal hypergraph models. The normal
    # one-stage decider-mux substitution is deliberately disabled until implementation choice
    # becomes an explicit solver variable.
    descriptions = {entity.description or "" for entity in planned.entities}
    assert not any(description.endswith(": true arm") for description in descriptions)
    assert not any(description.endswith(": false arm") for description in descriptions)
    assert roles.get("phase-delay.scalar-bus", 0) > 0
