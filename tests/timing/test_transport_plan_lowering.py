from factorio_circuit import Circuit, SamplingPolicy, SignalId
from factorio_circuit.analysis import (
    ExactTransportDemand,
    SharedTransportBus,
    SharedTransportLane,
    TemporalArc,
    TemporalHypergraph,
    TemporalPlacement,
    TemporalSink,
    TemporalSource,
    TemporalSourceMode,
    TransportOptimizationResult,
    analyze_temporal_alignment,
)
from factorio_circuit.compiler import lower_to_abstract_physical
from factorio_circuit.ir.abstract_physical import ArithmeticCombinator
from factorio_circuit.ir.semantic import PayloadShape
from factorio_circuit.lowering.transport_plan import ObservationAwareTransportLowerer

VALUE = SignalId("virtual", "signal-A")


def _state_backed_inputs() -> Circuit:
    circuit = Circuit("transport_plan_inputs")
    circuit.input("left")
    circuit.input("right")
    one = circuit.constant_signals({VALUE: 1})
    memory = circuit.accumulator("memory")

    deep = memory.sample()
    for _ in range(6):
        deep = deep + one
    memory.add(deep)

    circuit.step(1)
    circuit.output("memory", memory.sample())
    return circuit


def _baseline(policy: SamplingPolicy):
    result = lower_to_abstract_physical(
        _state_backed_inputs(),
        optimize=False,
        sampling_policy=policy,
    )
    period = result.state_timing.uniform_period
    assert period is not None
    assert period >= 5
    return result, period


def _all_private(transports: tuple[ExactTransportDemand, ...]) -> TransportOptimizationResult:
    scalar = sum(item.length for item in transports if item.shape is PayloadShape.SCALAR)
    vector = sum(item.length for item in transports if item.shape is PayloadShape.VECTOR)
    total = scalar + vector
    return TransportOptimizationResult(
        status="OPTIMAL",
        buses=(),
        private_transports=transports,
        bus_middle_stages=0,
        bus_interface_combinators=0,
        private_scalar_combinators=scalar,
        vector_combinators=vector,
        objective_combinators=total,
        best_bound=total,
        wall_time_seconds=0.0,
    )


def test_private_exact_transport_starts_after_last_free_observation() -> None:
    baseline, period = _baseline(SamplingPolicy.ALAP)
    source_semantic = baseline.optimized_ir.inputs[0]
    target = period - 1
    live_end = period - 3
    source = TemporalSource(
        id=1,
        label="left",
        shape=PayloadShape.SCALAR,
        mode=TemporalSourceMode.LIVE,
        start_phase=0,
        end_phase_exclusive=live_end,
        semantic=source_semantic,
    )
    sink = TemporalSink(2, "late", PayloadShape.SCALAR, target)
    graph = TemporalHypergraph(
        period=period,
        sources=(source,),
        computations=(),
        sinks=(sink,),
        arcs=(TemporalArc(1, 2, 0, PayloadShape.SCALAR),),
    )
    placement = TemporalPlacement(())
    alignment = analyze_temporal_alignment(graph, placement)
    assert len(alignment.transports) == 1
    demand = alignment.transports[0]
    assert demand.start_phase == live_end - 1
    assert demand.length == 3

    lowerer = ObservationAwareTransportLowerer(
        baseline.optimized_ir,
        state_timing=baseline.state_timing,
        sampling_policy=SamplingPolicy.ALAP,
        graph=graph,
        placement=placement,
        optimization=_all_private(alignment.transports),
    )
    lowerer._create_input_markers()
    source_value = lowerer.realize(source_semantic)
    before = len(lowerer.circuit.entities)

    result = lowerer.delay_to(source_value, target)

    assert result.phase == target
    # The physical source stays live for free until the planner's last-free phase.  Only the
    # residual three-tick chosen-token suffix is materialized.
    assert len(lowerer.circuit.entities) - before == demand.length
    delays = [
        entity
        for entity in lowerer.circuit.entities[before:]
        if isinstance(entity, ArithmeticCombinator)
        and entity.description == "phase alignment delay"
    ]
    assert len(delays) == demand.length


def test_shared_transport_uses_fresh_abstract_lanes_and_isolated_interfaces() -> None:
    baseline, period = _baseline(SamplingPolicy.BEGINNING_OF_STEP)
    left_semantic, right_semantic = baseline.optimized_ir.inputs[:2]
    target = 3
    assert target < period

    sources = (
        TemporalSource(
            1,
            "left",
            PayloadShape.SCALAR,
            TemporalSourceMode.EXACT,
            0,
            1,
            left_semantic,
        ),
        TemporalSource(
            2,
            "right",
            PayloadShape.SCALAR,
            TemporalSourceMode.EXACT,
            0,
            1,
            right_semantic,
        ),
    )
    sinks = (
        TemporalSink(3, "left-late", PayloadShape.SCALAR, target),
        TemporalSink(4, "right-late", PayloadShape.SCALAR, target),
    )
    graph = TemporalHypergraph(
        period=period,
        sources=sources,
        computations=(),
        sinks=sinks,
        arcs=(
            TemporalArc(1, 3, 0, PayloadShape.SCALAR),
            TemporalArc(2, 4, 0, PayloadShape.SCALAR),
        ),
    )
    placement = TemporalPlacement(())
    alignment = analyze_temporal_alignment(graph, placement)
    assert len(alignment.transports) == 2
    assert all(item.scalar_bus_candidate for item in alignment.transports)

    lanes = tuple(
        SharedTransportLane(
            lane_id=index,
            producer=item.producer,
            label=item.label,
            start_phase=item.start_phase,
            end_phase=item.end_phase,
            tap_phases=item.tap_phases,
        )
        for index, item in enumerate(alignment.transports, start=1)
    )
    bus = SharedTransportBus(
        index=0,
        start_phase=min(lane.ingress_phase for lane in lanes),
        end_phase=max(lane.trunk_end_phase for lane in lanes),
        lanes=lanes,
    )
    interfaces = sum(lane.interface_combinators for lane in lanes)
    objective = bus.middle_stages + interfaces
    optimization = TransportOptimizationResult(
        status="OPTIMAL",
        buses=(bus,),
        private_transports=(),
        bus_middle_stages=bus.middle_stages,
        bus_interface_combinators=interfaces,
        private_scalar_combinators=0,
        vector_combinators=0,
        objective_combinators=objective,
        best_bound=objective,
        wall_time_seconds=0.0,
    )

    lowerer = ObservationAwareTransportLowerer(
        baseline.optimized_ir,
        state_timing=baseline.state_timing,
        sampling_policy=SamplingPolicy.BEGINNING_OF_STEP,
        graph=graph,
        placement=placement,
        optimization=optimization,
    )
    lowerer._create_input_markers()
    left = lowerer.realize(left_semantic)
    right = lowerer.realize(right_semantic)
    before = len(lowerer.circuit.entities)

    left_out = lowerer.delay_to(left, target)
    right_out = lowerer.delay_to(right, target)

    # Two private three-tick chains would cost six combinators.  Isolated sharing uses two ingress
    # copies, one shared middle stage, and two egress copies.
    assert len(lowerer.circuit.entities) - before == objective == 5
    assert left_out.phase == right_out.phase == target
    assert left_out.signal not in {left.signal, right.signal}
    assert right_out.signal not in {left.signal, right.signal, left_out.signal}

    ingress = [
        entity
        for entity in lowerer.circuit.entities
        if isinstance(entity, ArithmeticCombinator)
        and entity.description == "phase alignment delay: bus ingress"
    ]
    middle = [
        entity
        for entity in lowerer.circuit.entities
        if isinstance(entity, ArithmeticCombinator)
        and entity.description == "shared exact transport bus 0"
    ]
    egress = [
        entity
        for entity in lowerer.circuit.entities
        if isinstance(entity, ArithmeticCombinator)
        and entity.description == "phase alignment delay: bus egress"
    ]
    assert len(ingress) == 2
    assert len(middle) == 1
    assert len(egress) == 2
    assert all(entity.left.signal != entity.output_signal for entity in ingress)
    assert all(entity.left.signal != entity.output_signal for entity in egress)

    ingress_nets = [
        builder
        for builder in lowerer.net_builders.values()
        if builder.label == "transport bus 0 isolated ingress @ 1"
    ]
    assert len(ingress_nets) == 1
    assert len(ingress_nets[0].signals) == 2
    trunk_nets = [
        builder
        for builder in lowerer.net_builders.values()
        if builder.label == "shared exact transport bus 0 @ 2"
    ]
    assert len(trunk_nets) == 1
    assert len(trunk_nets[0].signals) == 2

    bus_signals = set(ingress_nets[0].signals)
    conflict_pairs = {
        frozenset((conflict.left, conflict.right)) for conflict in lowerer.circuit.signal_conflicts
    }
    assert frozenset(bus_signals) in conflict_pairs
