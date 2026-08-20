from factorio_circuit import Circuit, SamplingPolicy, SignalId
from factorio_circuit.analysis import (
    DelayBusLane,
    DelayBusPlan,
    TemporalOptimizationResult,
    build_temporal_hypergraph,
    census_abstract_physical,
)
from factorio_circuit.compiler import lower_to_abstract_physical
from factorio_circuit.ir.abstract_physical import ArithmeticCombinator, Connector, Endpoint
from factorio_circuit.lowering.temporal_plan_isolated import (
    lower_normalized_vectors_with_isolated_temporal_plan,
)

VALUE = SignalId("virtual", "signal-A")


def _shared_controls() -> Circuit:
    circuit = Circuit("isolated_bus_shared_controls")
    left = circuit.input("left")
    right = circuit.input("right")
    one = circuit.constant_signals({VALUE: 1})
    memory = circuit.accumulator("memory")

    deep = memory.sample()
    for _ in range(6):
        deep = deep + one
    memory.add(deep)

    for source in (left, right):
        shared = source != 0
        late = shared
        for _ in range(3):
            late = late + 1
        memory.add(one, when=shared)
        memory.add(one, when=late)

    circuit.step(1)
    circuit.output("memory", memory.sample())
    return circuit


def test_shared_bus_never_connects_original_producer_net_directly() -> None:
    baseline = lower_to_abstract_physical(
        _shared_controls(),
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
        if item.delay_bus_eligible and item.length > 1
    ]
    assert len(intervals) >= 2

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

    planned = lower_normalized_vectors_with_isolated_temporal_plan(
        baseline.optimized_ir,
        state_timing=baseline.state_timing,
        sampling_policy=SamplingPolicy.ALAP,
        graph=graph,
        optimization=optimization,
    )

    census = census_abstract_physical(planned)
    roles = dict(census.lowering_roles)
    assert roles.get("phase-delay.scalar", 0) >= len(lanes)
    assert roles.get("phase-delay.scalar-bus", 0) > 0
    assert census.max_signals_per_net >= 2

    bus_stages = [
        entity
        for entity in planned.entities
        if isinstance(entity, ArithmeticCombinator)
        and (entity.description or "").startswith("scalar phase delay bus ")
    ]
    assert bus_stages

    # Every non-trunk net entering a shared bus stage must be an aggregate bus-private ingress net.
    # Original comparison/arithmetic producer nets must never touch the bus-stage input connector,
    # because same-color coalescing would contaminate those producer networks everywhere else they
    # are used. Multiple isolated lanes that join at the same phase intentionally share one ingress
    # net, so every stage has at most one such non-trunk input alongside the previous bus trunk.
    for stage in bus_stages:
        input_endpoint = Endpoint(stage.id, Connector.INPUT)
        ingress_nets = []
        for net in planned.nets:
            if input_endpoint not in net.endpoints:
                continue
            if net.label.startswith("scalar phase delay bus "):
                continue
            assert net.label.startswith("scalar delay bus 0 isolated ingress @ "), net.label
            ingress_nets.append(net)
        assert len(ingress_nets) <= 1

    ingress_nets = [
        net
        for net in planned.nets
        if net.label.startswith("scalar delay bus 0 isolated ingress @ ")
    ]
    assert len(ingress_nets) == len({lane.start_phase + 1 for lane in lanes})
    assert sum(len(net.signals) for net in ingress_nets) == len(lanes)
