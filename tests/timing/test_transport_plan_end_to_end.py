from factorio_circuit import Circuit, SamplingPolicy, SignalId
from factorio_circuit.analysis import (
    TransportOptimizationResult,
    analyze_temporal_alignment,
    build_temporal_hypergraph,
)
from factorio_circuit.compiler import lower_to_abstract_physical
from factorio_circuit.ir.semantic import PayloadShape
from factorio_circuit.lowering.transport_plan import (
    lower_normalized_vectors_with_observation_aware_transport,
)

VALUE = SignalId("virtual", "signal-A")


def _circuit() -> Circuit:
    circuit = Circuit("transport_plan_end_to_end")
    enabled = circuit.input("enabled")
    one = circuit.constant_signals({VALUE: 1})
    memory = circuit.accumulator("memory")

    deep = memory.sample()
    for _ in range(5):
        deep = deep + one
    memory.add(deep)
    memory.add(one, when=enabled != 0)

    circuit.step(1)
    circuit.output("memory", memory.sample())
    return circuit


def test_all_private_transport_plan_lowers_complete_periodic_module() -> None:
    baseline = lower_to_abstract_physical(
        _circuit(),
        optimize=False,
        sampling_policy=SamplingPolicy.BEGINNING_OF_STEP,
    )
    graph = build_temporal_hypergraph(
        baseline.optimized_ir,
        baseline.state_timing,
        sampling_policy=SamplingPolicy.BEGINNING_OF_STEP,
    )
    placement = graph.alap_placement()
    alignment = analyze_temporal_alignment(graph, placement)
    scalar = sum(
        item.length for item in alignment.transports if item.shape is PayloadShape.SCALAR
    )
    vector = sum(
        item.length for item in alignment.transports if item.shape is PayloadShape.VECTOR
    )
    total = scalar + vector
    optimization = TransportOptimizationResult(
        status="OPTIMAL",
        buses=(),
        private_transports=alignment.transports,
        bus_middle_stages=0,
        bus_interface_combinators=0,
        private_scalar_combinators=scalar,
        vector_combinators=vector,
        objective_combinators=total,
        best_bound=total,
        wall_time_seconds=0.0,
    )

    planned = lower_normalized_vectors_with_observation_aware_transport(
        baseline.optimized_ir,
        state_timing=baseline.state_timing,
        sampling_policy=SamplingPolicy.BEGINNING_OF_STEP,
        graph=graph,
        placement=placement,
        optimization=optimization,
    )

    planned.validate()
    assert planned.combinator_count > 0
    assert len(planned.inputs) == len(baseline.abstract_physical.inputs)
    assert len(planned.outputs) == len(baseline.abstract_physical.outputs)
