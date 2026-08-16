"""Reference-simulator contracts that are independent of physical Event lowering."""

import pytest

from factorio_circuit import (
    Circuit,
    EventMaterializationPolicy,
    EventOccurrence,
    EventSchedule,
    EventScheduleError,
    SignalId,
    materialize_event_trace,
    simulate_events,
)
from factorio_circuit.ir.semantic import ClockProvenance, PayloadShape, TemporalModality

IRON = SignalId("item", "iron-plate")


def test_event_declarations_preserve_shape_clock_and_namespace() -> None:
    circuit = Circuit("declarations")
    scalar = circuit.event("finished", guaranteed_min_separation=2)
    vector = circuit.signal_event("contents", guaranteed_min_separation=4)
    circuit.output("constant", 0)
    module = circuit.build()

    assert [source.name for source in module.event_inputs] == ["finished", "contents"]
    assert scalar.clock.provenance is ClockProvenance.EXTERNAL_EVENT
    assert scalar.flow.payload_shape is PayloadShape.SCALAR
    assert scalar.flow.modality is TemporalModality.EVENT
    assert scalar.flow.clock == scalar.clock
    assert vector.flow.payload_shape is PayloadShape.VECTOR
    assert vector.clock.guaranteed_min_separation == 4

    with pytest.raises(ValueError, match="already used"):
        circuit.event("finished", guaranteed_min_separation=1)


def test_schedules_cover_declared_sources_once_and_obey_clock_contracts() -> None:
    circuit = Circuit("schedules")
    first = circuit.event("first", guaranteed_min_separation=3)
    second = circuit.signal_event("second", guaranteed_min_separation=1)
    circuit.output("constant", 0)
    module = circuit.build()

    result = simulate_events(
        module,
        [],
        [EventSchedule(first, ()), EventSchedule(second, ())],
    )
    assert result.reactions == ()

    with pytest.raises(EventScheduleError, match="exactly one"):
        simulate_events(module, [], [EventSchedule(first, ())])
    with pytest.raises(EventScheduleError, match="duplicate"):
        simulate_events(module, [], [EventSchedule(first, ()), EventSchedule(first, ())])
    with pytest.raises(EventScheduleError, match="minimum separation"):
        simulate_events(
            module,
            [],
            [
                EventSchedule(
                    first,
                    (EventOccurrence(0, 0), EventOccurrence(2, 0)),
                ),
                EventSchedule(second, ()),
            ],
        )


@pytest.mark.parametrize(
    "occurrences",
    [
        (EventOccurrence(1, 0), EventOccurrence(1, 1)),
        (EventOccurrence(2, 0), EventOccurrence(1, 1)),
    ],
)
def test_schedule_timestamps_are_strictly_ordered(
    occurrences: tuple[EventOccurrence, ...],
) -> None:
    circuit = Circuit("ordered")
    event = circuit.event("event", guaranteed_min_separation=1)
    circuit.output("constant", 0)
    with pytest.raises(EventScheduleError, match="strictly ordered"):
        simulate_events(circuit.build(), [], [EventSchedule(event, occurrences)])


def test_event_payload_normalization_keeps_zero_and_empty_occurrences_present() -> None:
    circuit = Circuit("payloads")
    scalar = circuit.event("scalar", guaranteed_min_separation=1)
    vector = circuit.signal_event("vector", guaranteed_min_separation=1)
    circuit.output("constant", 0)

    result = simulate_events(
        circuit.build(),
        [],
        [
            EventSchedule(scalar, (EventOccurrence(0, 2**31),)),
            EventSchedule(vector, (EventOccurrence(0, {}),)),
        ],
    )

    assert [activation.payload for activation in result.reactions[0].activations] == [-(2**31), {}]


def test_same_timestamp_state_updates_observe_one_old_state_snapshot() -> None:
    circuit = Circuit("atomic_events")
    first = circuit.signal_event("first", guaranteed_min_separation=1)
    second = circuit.signal_event("second", guaranteed_min_separation=1)
    first_memory = circuit.freeze("first_memory")
    second_memory = circuit.freeze("second_memory")

    old_first = first_memory.sample()
    first_memory.capture_on(first, required_min_separation=1)
    second_memory.capture_on(second, old_first, required_min_separation=1)
    circuit.output("first", first_memory.sample())
    circuit.output("second", second_memory.sample())

    result = simulate_events(
        circuit.build(),
        [],
        [
            EventSchedule(second, (EventOccurrence(0, {IRON: 2}),)),
            EventSchedule(first, (EventOccurrence(0, {IRON: 1}),)),
        ],
    )

    reaction = result.reactions[0]
    assert [activation.source.name for activation in reaction.activations] == ["first", "second"]
    assert reaction.state_before == {"first_memory": {}, "second_memory": {}}
    assert result.final_state == {"first_memory": {IRON: 1}, "second_memory": {}}


def test_sample_on_materialization_distinguishes_presence_from_zero() -> None:
    circuit = Circuit("sample_on")
    value = circuit.input("value")
    trigger = circuit.event("trigger", guaranteed_min_separation=1)
    sampled = circuit.sample_on(value, trigger)
    circuit.output("sampled", sampled)

    result = simulate_events(
        circuit.build(),
        [{"value": 4}, {"value": 0}, {"value": 7}],
        [EventSchedule(trigger, (EventOccurrence(0, 0), EventOccurrence(2, 0)))],
        stop_timestamp=3,
    )

    hold = materialize_event_trace(result, sampled, EventMaterializationPolicy.HOLD)
    zero = materialize_event_trace(result, sampled, EventMaterializationPolicy.ZERO)
    valid = materialize_event_trace(result, sampled, EventMaterializationPolicy.VALID)
    assert hold.payloads == (4, 4, 7)
    assert zero.payloads == (4, 0, 7)
    assert valid.payloads == (4, 0, 7)
    assert valid.valid == (True, False, True)
