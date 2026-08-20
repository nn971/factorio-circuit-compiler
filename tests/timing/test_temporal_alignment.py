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
from factorio_circuit.ir.semantic import PayloadShape


def _source_to_sink(
    *,
    mode: TemporalSourceMode,
    start: int,
    end: int | None,
    use_phase: int,
) -> TemporalHypergraph:
    source = TemporalSource(
        id=1,
        label="source",
        shape=PayloadShape.SCALAR,
        mode=mode,
        start_phase=start,
        end_phase_exclusive=end,
        semantic=object(),
    )
    sink = TemporalSink(2, "sink", PayloadShape.SCALAR, use_phase)
    return TemporalHypergraph(
        period=max(use_phase + 1, 1),
        sources=(source,),
        computations=(),
        sinks=(sink,),
        arcs=(TemporalArc(1, 2, 0, PayloadShape.SCALAR),),
    )


def test_live_level_is_observed_late_without_transport_inside_window() -> None:
    graph = _source_to_sink(
        mode=TemporalSourceMode.LIVE,
        start=0,
        end=5,
        use_phase=3,
    )
    analysis = analyze_temporal_alignment(graph, TemporalPlacement(()))

    assert analysis.availability_for(1).kind is TemporalAvailabilityKind.OBSERVABLE
    assert analysis.uses[0].kind is TemporalAlignmentKind.OBSERVE_AT
    assert analysis.uses[0].phase == 3
    assert analysis.transports == ()


def test_transport_starts_at_last_free_live_observation() -> None:
    graph = _source_to_sink(
        mode=TemporalSourceMode.LIVE,
        start=0,
        end=5,
        use_phase=7,
    )
    analysis = analyze_temporal_alignment(graph, TemporalPlacement(()))

    assert analysis.uses[0].kind is TemporalAlignmentKind.TRANSPORT_TO
    assert analysis.uses[0].transport_start_phase == 4
    assert analysis.uses[0].transport_length == 3
    assert len(analysis.transports) == 1
    transport = analysis.transports[0]
    assert (transport.start_phase, transport.end_phase) == (4, 7)
    assert transport.scalar_bus_candidate


def test_exact_source_transport_cannot_move_its_capture_phase() -> None:
    graph = _source_to_sink(
        mode=TemporalSourceMode.EXACT,
        start=2,
        end=3,
        use_phase=6,
    )
    analysis = analyze_temporal_alignment(graph, TemporalPlacement(()))

    assert analysis.uses[0].kind is TemporalAlignmentKind.TRANSPORT_TO
    assert analysis.uses[0].transport_start_phase == 2
    assert (analysis.transports[0].start_phase, analysis.transports[0].end_phase) == (2, 6)


def test_live_derived_value_uses_bounded_stable_input_window() -> None:
    live = TemporalSource(
        id=1,
        label="live",
        shape=PayloadShape.SCALAR,
        mode=TemporalSourceMode.LIVE,
        start_phase=0,
        end_phase_exclusive=8,
        semantic=object(),
    )
    stable = TemporalSource(
        id=2,
        label="stable",
        shape=PayloadShape.SCALAR,
        mode=TemporalSourceMode.STABLE,
        start_phase=0,
        end_phase_exclusive=5,
        semantic=object(),
    )
    computation = TemporalComputation(
        id=3,
        label="derived",
        shape=PayloadShape.SCALAR,
        earliest_phase=2,
        latest_phase=2,
        semantic=object(),
    )
    free_sink = TemporalSink(4, "free", PayloadShape.SCALAR, 5)
    late_sink = TemporalSink(5, "late", PayloadShape.SCALAR, 7)
    graph = TemporalHypergraph(
        period=8,
        sources=(live, stable),
        computations=(computation,),
        sinks=(free_sink, late_sink),
        arcs=(
            TemporalArc(1, 3, 1, PayloadShape.SCALAR),
            TemporalArc(2, 3, 1, PayloadShape.SCALAR),
            TemporalArc(3, 4, 0, PayloadShape.SCALAR),
            TemporalArc(3, 5, 0, PayloadShape.SCALAR),
        ),
    )

    analysis = analyze_temporal_alignment(graph, TemporalPlacement(((3, 2),)))
    derived = analysis.availability_for(3)

    assert derived.kind is TemporalAvailabilityKind.OBSERVABLE
    assert (derived.start_phase, derived.end_phase_exclusive) == (2, 6)
    uses = [item for item in analysis.uses if item.producer == 3]
    assert [item.kind for item in uses] == [
        TemporalAlignmentKind.OBSERVE_AT,
        TemporalAlignmentKind.TRANSPORT_TO,
    ]
    assert uses[1].transport_start_phase == 5
    assert (analysis.transports[0].start_phase, analysis.transports[0].end_phase) == (5, 7)
