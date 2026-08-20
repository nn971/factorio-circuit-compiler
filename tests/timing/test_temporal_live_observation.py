from __future__ import annotations

from importlib.util import find_spec
from typing import cast

import pytest

from factorio_circuit import Circuit, SamplingPolicy, SignalId
from factorio_circuit.analysis import (
    LiveSourceObservation,
    TemporalArc,
    TemporalComputation,
    TemporalHypergraph,
    TemporalOptimizationResult,
    TemporalPlacement,
    TemporalSink,
    TemporalSource,
    TemporalSourceMode,
    build_temporal_hypergraph,
    optimize_temporal_hypergraph,
)
from factorio_circuit.compiler import lower_to_abstract_physical
from factorio_circuit.ir.semantic import PayloadShape, Value
from factorio_circuit.lowering.temporal_plan import TemporalPlanLowerer

VALUE = SignalId("virtual", "signal-A")


def _two_phase_live_scalar() -> Circuit:
    circuit = Circuit("two_phase_live_scalar")
    source = circuit.input("source")
    one = circuit.constant_signals({VALUE: 1})
    memory = circuit.accumulator("memory")

    # Give the state domain enough physical slack that two direct reads of the same live input can
    # naturally be consumed at different phases.
    deep = memory.sample()
    for _ in range(7):
        deep = deep + one
    memory.add(deep)

    late = source != 0
    early = source > 0
    for _ in range(3):
        early = early + 1

    memory.add(one, when=late)
    memory.add(one, when=early)
    circuit.step(1)
    circuit.output("memory", memory.sample())
    return circuit


def _observation_for_source(
    graph: TemporalHypergraph,
    placement: TemporalPlacement,
    source: TemporalSource,
) -> LiveSourceObservation:
    phases = dict(placement.phases)
    computation_ids = graph.computation_ids
    sinks = {item.id: item for item in graph.sinks}
    uses = [arc for arc in graph.arcs if arc.producer == source.id]
    inputs = [
        phases[arc.consumer] - arc.latency
        if arc.consumer in computation_ids
        else sinks[arc.consumer].phase
        for arc in uses
    ]
    return LiveSourceObservation(
        source=source.id,
        label=source.label,
        shape=source.shape,
        phase=min(inputs),
        end_phase=max(inputs),
    )


def test_temporal_lowerer_transports_one_coherent_live_observation() -> None:
    baseline = lower_to_abstract_physical(
        _two_phase_live_scalar(),
        optimize=False,
        sampling_policy=SamplingPolicy.ALAP,
    )
    graph = build_temporal_hypergraph(
        baseline.optimized_ir,
        baseline.state_timing,
        sampling_policy=SamplingPolicy.ALAP,
    )
    source = next(
        item
        for item in graph.sources
        if item.mode is TemporalSourceMode.LIVE and item.shape is PayloadShape.SCALAR
    )
    placement = graph.alap_placement()
    observation = _observation_for_source(graph, placement, source)
    assert observation.end_phase > observation.phase

    optimization = TemporalOptimizationResult(
        status="FEASIBLE",
        placement=placement,
        buses=(),
        bus_stages=0,
        ordinary_scalar_delays=observation.transport_stages,
        vector_delays=0,
        objective_delays=observation.transport_stages,
        best_bound=0,
        wall_time_seconds=0.0,
        live_source_observations=(observation,),
    )
    lowerer = TemporalPlanLowerer(
        baseline.optimized_ir,
        state_timing=baseline.state_timing,
        sampling_policy=SamplingPolicy.ALAP,
        graph=graph,
        optimization=optimization,
    )
    lowerer._create_input_markers()

    raw = lowerer.realize(cast(Value, source.semantic))
    at_observation = lowerer.delay_to(raw, observation.phase)
    entities_before_transport = len(lowerer.circuit.entities)
    at_latest_use = lowerer.delay_to(raw, observation.end_phase)

    assert at_observation.net == raw.net
    assert at_observation.phase == observation.phase
    assert at_latest_use.net != raw.net
    assert at_latest_use.phase == observation.end_phase
    assert (
        len(lowerer.circuit.entities) - entities_before_transport
        == observation.transport_stages
    )


@pytest.mark.skipif(find_spec("ortools") is None, reason="OR-Tools is an optional dependency")
def test_optimizer_chooses_one_live_observation_for_all_consumers() -> None:
    source = TemporalSource(
        id=1,
        label="live-control",
        shape=PayloadShape.SCALAR,
        mode=TemporalSourceMode.LIVE,
        start_phase=0,
        end_phase_exclusive=8,
        semantic=object(),
    )
    first = TemporalComputation(
        id=2,
        label="first",
        shape=PayloadShape.SCALAR,
        earliest_phase=1,
        latest_phase=3,
        semantic=object(),
    )
    second = TemporalComputation(
        id=3,
        label="second",
        shape=PayloadShape.SCALAR,
        earliest_phase=5,
        latest_phase=5,
        semantic=object(),
    )
    sink_first = TemporalSink(4, "first-sink", PayloadShape.SCALAR, 3)
    sink_second = TemporalSink(5, "second-sink", PayloadShape.SCALAR, 5)
    graph = TemporalHypergraph(
        period=8,
        sources=(source,),
        computations=(first, second),
        sinks=(sink_first, sink_second),
        arcs=(
            TemporalArc(1, 2, 1, PayloadShape.SCALAR),
            TemporalArc(1, 3, 1, PayloadShape.SCALAR),
            TemporalArc(2, 4, 0, PayloadShape.SCALAR),
            TemporalArc(3, 5, 0, PayloadShape.SCALAR),
        ),
    )

    result = optimize_temporal_hypergraph(graph, time_limit_seconds=5, workers=1)

    assert result.placement.phase_for(2) == 3
    assert result.placement.phase_for(3) == 5
    assert result.live_source_observations == (
        LiveSourceObservation(
            source=1,
            label="live-control",
            shape=PayloadShape.SCALAR,
            phase=2,
            end_phase=4,
        ),
    )
    assert result.ordinary_scalar_delays == 2
