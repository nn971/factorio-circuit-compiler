import pytest

from factorio_circuit import (
    Circuit,
    EventCrossingError,
    EventMaterializationPolicy,
    EventOccurrence,
    EventSchedule,
    EventScheduleError,
    SignalId,
    materialize_event_trace,
    simulate_events,
)
from factorio_circuit.ir.clocks import SumInto
from factorio_circuit.ir.state import AccumulatorRegister, state_transitions

SIGNAL = SignalId("virtual", "signal-test")


def test_sum_into_is_explicit_packed_stateful_bridge() -> None:
    circuit = Circuit("sum_ir")
    source = circuit.signal_event("source", guaranteed_min_separation=2)
    target = circuit.event("target", guaranteed_min_separation=3)

    summed = circuit.sum_into(source, target)
    module = circuit.build()

    assert isinstance(summed.ir, SumInto)
    assert summed.ir.source == source.ir
    assert summed.ir.target == target.ir
    assert summed.clock == target.clock
    assert summed.ir.payload_shape.value == "vector"
    assert len(module.state_registers) == 1
    assert summed.ir.register == module.state_registers[0]
    assert isinstance(summed.ir.register, AccumulatorRegister)
    assert summed.ir.register.name == "sum0_buffer"

    transitions = state_transitions(module)
    assert [(item.kind, item.trigger, item.order) for item in transitions] == [
        ("add", source.ir, 0),
        ("clear", target.ir, 1),
    ]


def test_sum_into_is_interned_and_pays_for_one_vector_accumulator() -> None:
    circuit = Circuit("sum_cse")
    source = circuit.signal_event("source", guaranteed_min_separation=2)
    target = circuit.event("target", guaranteed_min_separation=2)

    first = circuit.sum_into(source, target)
    second = circuit.sum_into(source, target)
    module = circuit.build()

    assert first.ir is second.ir
    assert len(module.state_registers) == 1
    assert len(state_transitions(module)) == 2
    assert sum(isinstance(item, SumInto) for item in module.event_inputs) == 1


def test_sum_into_uses_right_closed_target_intervals_and_keeps_tail_buffered() -> None:
    circuit = Circuit("sum_behavior")
    source = circuit.signal_event("source", guaranteed_min_separation=2)
    target = circuit.event("target", guaranteed_min_separation=2)
    summed = circuit.sum_into(source, target)

    result = simulate_events(
        circuit.build(),
        (),
        (
            EventSchedule(
                source,
                (
                    EventOccurrence(0, {SIGNAL: 2}),
                    EventOccurrence(2, {SIGNAL: 3}),
                    EventOccurrence(4, {SIGNAL: 5}),
                    EventOccurrence(7, {SIGNAL: 11}),
                ),
            ),
            EventSchedule(
                target,
                (
                    EventOccurrence(2, 1),
                    EventOccurrence(5, 1),
                ),
            ),
        ),
        stop_timestamp=9,
    )
    trace = materialize_event_trace(result, summed, EventMaterializationPolicy.VALID)

    # The t=2 source occurrence is part of (-inf, 2], not the next interval.
    assert trace.payloads[2] == {SIGNAL: 5}
    assert trace.payloads[5] == {SIGNAL: 5}
    assert trace.valid == (False, False, True, False, False, True, False, False, False)
    # The t=7 source occurrence lies after the last target and remains buffered.
    assert result.final_state == {"sum0_buffer": {SIGNAL: 11}}


def test_sum_into_emits_present_empty_vector_when_interval_has_no_source_events() -> None:
    circuit = Circuit("sum_empty")
    source = circuit.signal_event("source", guaranteed_min_separation=2)
    target = circuit.event("target", guaranteed_min_separation=2)
    summed = circuit.sum_into(source, target)

    result = simulate_events(
        circuit.build(),
        (),
        (
            EventSchedule(source, ()),
            EventSchedule(target, (EventOccurrence(1, 1), EventOccurrence(4, 1))),
        ),
        stop_timestamp=6,
    )
    trace = materialize_event_trace(result, summed, EventMaterializationPolicy.VALID)

    assert trace.payloads[1] == {}
    assert trace.payloads[4] == {}
    assert trace.valid == (False, True, False, False, True, False)


def test_sum_into_accepts_event_merge_source_and_gate_clock_target() -> None:
    circuit = Circuit("sum_derived")
    left = circuit.signal_event("left", guaranteed_min_separation=2)
    right = circuit.signal_event("right", guaranteed_min_separation=2)
    tick = circuit.event("tick", guaranteed_min_separation=2)
    enabled = circuit.input("enabled")
    merged = circuit.event_merge(left, right)
    gated = circuit.gate_clock(tick, when=circuit.sample_on(enabled, tick))
    assert not isinstance(merged, tuple)
    summed = circuit.sum_into(merged, gated)  # type: ignore[arg-type]

    result = simulate_events(
        circuit.build(),
        (
            {"enabled": 0},
            {"enabled": 0},
            {"enabled": 1},
            {"enabled": 0},
            {"enabled": 1},
            {"enabled": 0},
        ),
        (
            EventSchedule(left, (EventOccurrence(1, {SIGNAL: 2}),)),
            EventSchedule(right, (EventOccurrence(3, {SIGNAL: 5}),)),
            EventSchedule(tick, (EventOccurrence(2, 1), EventOccurrence(4, 1))),
        ),
        stop_timestamp=6,
    )
    trace = materialize_event_trace(result, summed, EventMaterializationPolicy.VALID)

    assert trace.payloads[2] == {SIGNAL: 2}
    assert trace.payloads[4] == {SIGNAL: 5}


def test_sum_into_output_can_drive_target_clock_state() -> None:
    circuit = Circuit("sum_consumer")
    source = circuit.signal_event("source", guaranteed_min_separation=2)
    target = circuit.event("target", guaranteed_min_separation=2)
    summed = circuit.sum_into(source, target)
    output = circuit.freeze("output")
    output.set(summed._as_signals(), when=1)

    result = simulate_events(
        circuit.build(),
        (),
        (
            EventSchedule(
                source,
                (
                    EventOccurrence(0, {SIGNAL: 3}),
                    EventOccurrence(2, {SIGNAL: 4}),
                ),
            ),
            EventSchedule(target, (EventOccurrence(2, 1),)),
        ),
        stop_timestamp=4,
    )

    assert result.final_state == {
        "sum0_buffer": {},
        "output": {SIGNAL: 7},
    }


def test_sum_into_rejects_non_vector_or_same_clock_sources() -> None:
    circuit = Circuit("sum_errors")
    scalar = circuit.event("scalar", guaranteed_min_separation=2)
    vector = circuit.signal_event("vector", guaranteed_min_separation=2)
    level = circuit.signals("level")
    target = circuit.event("target", guaranteed_min_separation=2)

    with pytest.raises(EventCrossingError, match="vector Event source"):
        circuit.sum_into(scalar, target)  # type: ignore[arg-type]
    with pytest.raises(EventCrossingError, match="vector Event source"):
        circuit.sum_into(level, target)  # type: ignore[arg-type]
    with pytest.raises(EventCrossingError, match="distinct source and target clocks"):
        circuit.sum_into(vector, vector)


def test_sum_into_derived_schedule_cannot_be_supplied_by_caller() -> None:
    circuit = Circuit("sum_schedule")
    source = circuit.signal_event("source", guaranteed_min_separation=2)
    target = circuit.event("target", guaranteed_min_separation=2)
    summed = circuit.sum_into(source, target)

    with pytest.raises(EventScheduleError, match="derived Event schedules are synthesized"):
        simulate_events(
            circuit.build(),
            (),
            (
                EventSchedule(source, ()),
                EventSchedule(target, ()),
                EventSchedule(summed, ()),
            ),
            stop_timestamp=1,
        )
