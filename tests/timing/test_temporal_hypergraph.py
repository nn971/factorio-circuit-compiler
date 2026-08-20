import pytest

from factorio_circuit import Circuit, SamplingPolicy, SignalId
from factorio_circuit.analysis import (
    FACTORIO_LATENCY,
    TemporalArc,
    TemporalComputation,
    TemporalHypergraph,
    TemporalSink,
    TemporalSource,
    TemporalSourceMode,
    build_temporal_hypergraph,
    optimize_temporal_hypergraph,
)
from factorio_circuit.analysis.temporal_builder import _TimingExactTemporalHypergraphBuilder
from factorio_circuit.compiler import lower_to_abstract_physical
from factorio_circuit.ir.semantic import BinaryOp, Compare, Constant, PayloadShape, Select

VALUE = SignalId("virtual", "signal-A")


def _shared_scalar_fanout() -> Circuit:
    c = Circuit("temporal_shared_scalar_fanout")
    enabled = c.input("enabled")
    one = c.constant_signals({VALUE: 1})
    memory = c.accumulator("memory")

    # Put the state domain on a genuine multicycle critical recurrence. At the minimum feasible
    # period this can pin every computation to one phase; the important property for this fixture is
    # the shared scalar lifetime, not artificial scheduling slack.
    deep = memory.sample()
    for _ in range(5):
        deep = deep + one
    memory.add(deep)

    # ``shared`` is needed both directly at the state boundary and through a deeper scalar chain.
    # Even when the minimum-period schedule pins all computation phases, one physical realization of
    # ``shared`` must survive until its direct late consumer. The temporal hypergraph should expose
    # that lifetime instead of hiding it inside emitted phase-delay combinators.
    shared = enabled != 0
    late = shared
    for _ in range(3):
        late = late + 1
    memory.add(one, when=shared)
    memory.add(one, when=late)

    c.step(1)
    c.output("memory", memory.sample())
    return c


def test_temporal_hypergraph_exposes_shared_scalar_transport() -> None:
    lowered = lower_to_abstract_physical(
        _shared_scalar_fanout(),
        optimize=False,
        sampling_policy=SamplingPolicy.ALAP,
    )
    graph = build_temporal_hypergraph(
        lowered.optimized_ir,
        lowered.state_timing,
        sampling_policy=SamplingPolicy.ALAP,
    )

    assert graph.period > 1
    assert graph.computations

    asap = graph.transport_cost(graph.asap_placement())
    alap = graph.transport_cost(graph.alap_placement())
    assert asap.bus_eligible_scalar_serial > 0
    assert alap.bus_eligible_scalar_serial > 0
    assert all(item.end_phase > item.start_phase for item in alap.intervals)


def test_temporal_select_uses_distinct_condition_and_data_latencies() -> None:
    condition = Compare("==", Constant(1), Constant(1))
    when_true = Constant(7)
    when_false = Constant(9)
    select = Select(condition, when_true, when_false)

    children = _TimingExactTemporalHypergraphBuilder._children(select)

    assert children == (
        (
            condition,
            FACTORIO_LATENCY.operation_latency("select_condition", select.name),
        ),
        (
            when_true,
            FACTORIO_LATENCY.operation_latency("select_data", select.name),
        ),
        (
            when_false,
            FACTORIO_LATENCY.operation_latency("select_data", select.name),
        ),
    )


def test_temporal_hypergraph_represents_real_phase_mobility_when_slack_exists() -> None:
    source = TemporalSource(
        id=1,
        label="source",
        shape=PayloadShape.SCALAR,
        mode=TemporalSourceMode.LIVE,
        start_phase=0,
        end_phase_exclusive=8,
        semantic=object(),
    )
    semantic = BinaryOp("+", Constant(1), Constant(1), name="movable")
    computation = TemporalComputation(
        id=2,
        label="movable",
        shape=PayloadShape.SCALAR,
        earliest_phase=1,
        latest_phase=5,
        semantic=semantic,
    )
    sink = TemporalSink(3, "sink", PayloadShape.SCALAR, 6)
    graph = TemporalHypergraph(
        period=8,
        sources=(source,),
        computations=(computation,),
        sinks=(sink,),
        arcs=(
            TemporalArc(source.id, computation.id, 1, PayloadShape.SCALAR),
            TemporalArc(computation.id, sink.id, 0, PayloadShape.SCALAR),
        ),
    )

    assert computation.mobility == 4
    assert graph.asap_placement().phase_for(computation) == 1
    assert graph.alap_placement().phase_for(computation) == 5
    graph.validate_placement(graph.asap_placement())
    graph.validate_placement(graph.alap_placement())


def _fixed_bus_graph(
    intervals: tuple[tuple[int, int], ...],
) -> TemporalHypergraph:
    sources = []
    computations = []
    sinks = []
    arcs = []
    next_id = 1
    for index, (start, end) in enumerate(intervals):
        source = TemporalSource(
            id=next_id,
            label=f"sample_{index}",
            shape=PayloadShape.SCALAR,
            mode=TemporalSourceMode.EXACT,
            start_phase=start,
            end_phase_exclusive=start + 1,
            semantic=object(),
        )
        next_id += 1
        semantic = BinaryOp("+", Constant(index), Constant(1), name=f"value_{index}")
        computation = TemporalComputation(
            id=next_id,
            label=f"value_{index}",
            shape=PayloadShape.SCALAR,
            earliest_phase=start,
            latest_phase=start,
            semantic=semantic,
        )
        next_id += 1
        sink = TemporalSink(next_id, f"sink_{index}", PayloadShape.SCALAR, end)
        next_id += 1
        sources.append(source)
        computations.append(computation)
        sinks.append(sink)
        arcs.append(TemporalArc(source.id, computation.id, 0, PayloadShape.SCALAR))
        arcs.append(TemporalArc(computation.id, sink.id, 0, PayloadShape.SCALAR))
    return TemporalHypergraph(
        period=max((end for _start, end in intervals), default=1) + 1,
        sources=tuple(sources),
        computations=tuple(computations),
        sinks=tuple(sinks),
        arcs=tuple(arcs),
    )


def test_exact_bus_solver_shares_overlapping_lifetimes() -> None:
    pytest.importorskip("ortools.sat.python.cp_model")
    graph = _fixed_bus_graph(((0, 10), (5, 10)))

    result = optimize_temporal_hypergraph(
        graph,
        bus_capacity=2,
        max_buses=2,
        time_limit_seconds=5.0,
    )

    assert result.proven_optimal
    assert result.objective_delays == 10
    assert result.bus_stages == 10
    assert len(result.buses) == 1
    assert len(result.buses[0].lanes) == 2


def test_exact_bus_solver_splits_disjoint_lifetimes() -> None:
    pytest.importorskip("ortools.sat.python.cp_model")
    graph = _fixed_bus_graph(((0, 2), (8, 10)))

    result = optimize_temporal_hypergraph(
        graph,
        bus_capacity=2,
        max_buses=2,
        time_limit_seconds=5.0,
    )

    assert result.proven_optimal
    assert result.objective_delays == 4
    assert result.bus_stages == 4
    assert len(result.buses) == 2
