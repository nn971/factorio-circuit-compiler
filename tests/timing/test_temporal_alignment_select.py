from factorio_circuit.analysis import (
    TemporalAlignmentKind,
    TemporalArc,
    TemporalAvailabilityKind,
    TemporalComputation,
    TemporalHypergraph,
    TemporalPlacement,
    TemporalSink,
    TemporalSource,
    TemporalSourceMode,
    analyze_temporal_alignment,
)
from factorio_circuit.ir.semantic import Constant, PayloadShape, Select


def test_scalar_select_is_exact_and_uses_condition_latency_boundary() -> None:
    condition = Constant(1)
    select = Select(condition, Constant(10), Constant(20), name="choice")
    condition_source = TemporalSource(
        id=1,
        label="condition",
        shape=PayloadShape.SCALAR,
        mode=TemporalSourceMode.EXACT,
        start_phase=0,
        end_phase_exclusive=1,
        semantic=condition,
    )
    select_computation = TemporalComputation(
        id=2,
        label="choice",
        shape=PayloadShape.SCALAR,
        earliest_phase=5,
        latest_phase=5,
        semantic=select,
    )
    sink = TemporalSink(3, "late", PayloadShape.SCALAR, 7)
    graph = TemporalHypergraph(
        period=8,
        sources=(condition_source,),
        computations=(select_computation,),
        sinks=(sink,),
        arcs=(
            # The hypergraph's conservative scheduling envelope still carries the three-tick data
            # latency on this edge.  Alignment refines the physical Select condition use to the
            # target model's two-tick condition boundary.
            TemporalArc(1, 2, 3, PayloadShape.SCALAR),
            TemporalArc(2, 3, 0, PayloadShape.SCALAR),
        ),
    )
    placement = TemporalPlacement(((2, 5),))

    analysis = analyze_temporal_alignment(graph, placement)

    select_availability = analysis.availability_for(2)
    assert select_availability.kind is TemporalAvailabilityKind.EXACT
    assert (select_availability.start_phase, select_availability.end_phase_exclusive) == (5, 6)

    condition_use = next(item for item in analysis.uses if item.producer == 1)
    assert condition_use.kind is TemporalAlignmentKind.TRANSPORT_TO
    assert condition_use.phase == 3
    assert condition_use.transport_start_phase == 0

    select_use = next(item for item in analysis.uses if item.producer == 2)
    assert select_use.kind is TemporalAlignmentKind.TRANSPORT_TO
    assert select_use.phase == 7
    assert select_use.transport_start_phase == 5
