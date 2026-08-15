import pytest

from factorio_circuit import (
    Circuit,
    EventCrossingError,
    EventOccurrence,
    EventSchedule,
    EventScheduleError,
    SignalId,
    simulate_events,
)
from factorio_circuit.ir.clocks import EventMerge
from factorio_circuit.ir.semantic import ClockProvenance, PayloadShape

SIGNAL = SignalId("virtual", "signal-test")


def _activation_payloads(result: object, source: object) -> list[tuple[int, object]]:
    return [
        (reaction.timestamp, activation.payload)
        for reaction in result.reactions  # type: ignore[attr-defined]
        for activation in reaction.activations
        if activation.source == source
    ]


def test_event_merge_is_additive_derived_union_with_conservative_contract() -> None:
    circuit = Circuit("merge_contract")
    left = circuit.event("left", guaranteed_min_separation=10)
    right = circuit.event("right", guaranteed_min_separation=10)

    merged = circuit.event_merge(left, right)

    assert isinstance(merged.ir, EventMerge)
    assert merged.ir.parents == (left.ir, right.ir)
    assert merged.ir.payload_shape is PayloadShape.SCALAR
    assert merged.clock.provenance is ClockProvenance.DERIVED
    assert merged.clock.guaranteed_min_separation == 1


def test_event_merge_is_commutative_interned_and_flattens_nested_unions() -> None:
    circuit = Circuit("merge_cse")
    first = circuit.event("first", guaranteed_min_separation=2)
    second = circuit.event("second", guaranteed_min_separation=3)
    third = circuit.event("third", guaranteed_min_separation=4)

    first_second = circuit.event_merge(first, second)
    second_first = circuit.event_merge(second, first)
    nested = circuit.event_merge(first_second, third)
    flat = circuit.event_merge(third, second, first)

    assert first_second.ir is second_first.ir
    assert nested.ir is flat.ir
    assert isinstance(nested.ir, EventMerge)
    assert nested.ir.parents == (first.ir, second.ir, third.ir)


def test_duplicate_merge_parent_is_set_like_and_needs_no_new_clock() -> None:
    circuit = Circuit("merge_duplicate")
    source = circuit.event("source", guaranteed_min_separation=7)

    merged = circuit.event_merge(source, source)

    assert merged.ir is source.ir
    assert circuit.build().event_inputs == (source.ir,)


def test_event_merge_rejects_mixed_payload_shapes() -> None:
    circuit = Circuit("merge_shape")
    scalar = circuit.event("scalar", guaranteed_min_separation=2)
    vector = circuit.signal_event("vector", guaranteed_min_separation=2)

    with pytest.raises(EventCrossingError, match="one payload shape"):
        circuit.event_merge(scalar, vector)


def test_scalar_event_merge_coalesces_simultaneous_parents_by_addition() -> None:
    circuit = Circuit("merge_scalar")
    left = circuit.event("left", guaranteed_min_separation=2)
    right = circuit.event("right", guaranteed_min_separation=2)
    merged = circuit.event_merge(left, right)

    result = simulate_events(
        circuit.build(),
        (),
        (
            EventSchedule(
                left,
                (
                    EventOccurrence(1, 4),
                    EventOccurrence(4, 7),
                ),
            ),
            EventSchedule(
                right,
                (
                    EventOccurrence(2, 3),
                    EventOccurrence(4, -2),
                ),
            ),
        ),
        stop_timestamp=6,
    )

    assert _activation_payloads(result, merged.ir) == [(1, 4), (2, 3), (4, 5)]


def test_vector_event_merge_adds_payloads_and_can_drive_state_directly() -> None:
    circuit = Circuit("merge_vector_state")
    left = circuit.signal_event("left", guaranteed_min_separation=2)
    right = circuit.signal_event("right", guaranteed_min_separation=2)
    merged = circuit.event_merge(left, right)
    memory = circuit.freeze("memory")
    memory.set(merged * 1, when=1)

    result = simulate_events(
        circuit.build(),
        (),
        (
            EventSchedule(
                left,
                (
                    EventOccurrence(1, {SIGNAL: 2}),
                    EventOccurrence(4, {SIGNAL: 4}),
                ),
            ),
            EventSchedule(
                right,
                (
                    EventOccurrence(2, {SIGNAL: 3}),
                    EventOccurrence(4, {SIGNAL: 5}),
                ),
            ),
        ),
        stop_timestamp=6,
    )

    assert _activation_payloads(result, merged.ir) == [
        (1, {SIGNAL: 2}),
        (2, {SIGNAL: 3}),
        (4, {SIGNAL: 9}),
    ]
    assert result.final_state == {"memory": {SIGNAL: 9}}


def test_vector_merge_keeps_zero_payload_as_a_present_occurrence() -> None:
    circuit = Circuit("merge_zero")
    left = circuit.signal_event("left", guaranteed_min_separation=2)
    right = circuit.signal_event("right", guaranteed_min_separation=2)
    merged = circuit.event_merge(left, right)

    result = simulate_events(
        circuit.build(),
        (),
        (
            EventSchedule(left, (EventOccurrence(2, {SIGNAL: 8}),)),
            EventSchedule(right, (EventOccurrence(2, {SIGNAL: -8}),)),
        ),
        stop_timestamp=4,
    )

    assert _activation_payloads(result, merged.ir) == [(2, {})]


def test_event_merge_can_feed_a_gate_clock() -> None:
    circuit = Circuit("merge_then_gate")
    left = circuit.event("left", guaranteed_min_separation=2)
    right = circuit.event("right", guaranteed_min_separation=2)
    merged = circuit.event_merge(left, right)
    positive = circuit.gate_clock(merged, when=merged > 0)  # type: ignore[operator]

    result = simulate_events(
        circuit.build(),
        (),
        (
            EventSchedule(left, (EventOccurrence(1, 2), EventOccurrence(4, -5))),
            EventSchedule(right, (EventOccurrence(2, 3), EventOccurrence(4, 1))),
        ),
        stop_timestamp=6,
    )

    assert _activation_payloads(result, positive.ir) == [(1, 1), (2, 1)]


def test_event_merge_schedule_cannot_be_supplied_by_the_caller() -> None:
    circuit = Circuit("merge_schedule")
    left = circuit.event("left", guaranteed_min_separation=2)
    right = circuit.event("right", guaranteed_min_separation=2)
    merged = circuit.event_merge(left, right)

    with pytest.raises(EventScheduleError, match="cannot be supplied externally"):
        simulate_events(
            circuit.build(),
            (),
            (
                EventSchedule(left, ()),
                EventSchedule(right, ()),
                EventSchedule(merged, (EventOccurrence(1, 1),)),
            ),
            stop_timestamp=2,
        )
