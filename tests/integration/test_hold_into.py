import pytest

from factorio_circuit import (
    Circuit,
    EventCrossingError,
    EventMaterializationPolicy,
    EventOccurrence,
    EventSchedule,
    SignalId,
    materialize_event_trace,
    simulate_events,
)
from factorio_circuit.ir.semantic import SampleOn, TemporalModality
from factorio_circuit.ir.state import FreezeRegister, VectorRegisterRead, state_transitions

SIGNAL = SignalId("virtual", "signal-test")


def test_hold_into_elaborates_to_explicit_freeze_state_and_sample_on() -> None:
    circuit = Circuit("hold_ir")
    source = circuit.signal_event("source", guaranteed_min_separation=2)
    target = circuit.event("target", guaranteed_min_separation=3)

    held = circuit.hold_into(source, target)
    module = circuit.build()

    assert isinstance(held.ir, SampleOn)
    assert held.target == target.ir
    assert held.payload_shape.value == "vector"
    assert held.ir.flow is not None
    assert held.ir.flow.modality is TemporalModality.EVENT
    assert held.ir.flow.clock == target.clock

    assert len(module.state_registers) == 1
    register = module.state_registers[0]
    assert isinstance(register, FreezeRegister)
    assert register.name == "hold0"
    assert isinstance(held.source, VectorRegisterRead)
    assert held.source.register == register

    transitions = state_transitions(module)
    assert len(transitions) == 1
    transition = transitions[0]
    assert transition.register == register
    assert transition.kind == "set"
    assert transition.trigger == source.ir
    assert transition.clock == source.clock
    assert module.sample_on_crossings == (held.ir,)


def test_hold_into_is_interned_and_pays_for_state_once() -> None:
    circuit = Circuit("hold_cse")
    source = circuit.signal_event("source", guaranteed_min_separation=2)
    target = circuit.event("target", guaranteed_min_separation=2)

    first = circuit.hold_into(source, target)
    second = circuit.hold_into(source, target)
    module = circuit.build()

    assert first.ir is second.ir
    assert len(module.state_registers) == 1
    assert len(state_transitions(module)) == 1
    assert module.sample_on_crossings == (first.ir,)


def test_hold_into_emits_latest_strictly_prior_source_value_on_target_clock() -> None:
    circuit = Circuit("hold_behavior")
    source = circuit.signal_event("source", guaranteed_min_separation=2)
    target = circuit.event("target", guaranteed_min_separation=2)
    held = circuit.hold_into(source, target)

    result = simulate_events(
        circuit.build(),
        (),
        (
            EventSchedule(
                source,
                (
                    EventOccurrence(1, {SIGNAL: 10}),
                    EventOccurrence(4, {SIGNAL: 40}),
                    EventOccurrence(7, {SIGNAL: 70}),
                ),
            ),
            EventSchedule(
                target,
                (
                    EventOccurrence(2, 1),
                    EventOccurrence(4, 1),
                    EventOccurrence(6, 1),
                    EventOccurrence(8, 1),
                ),
            ),
        ),
        stop_timestamp=10,
    )
    trace = materialize_event_trace(result, held, EventMaterializationPolicy.VALID)

    assert trace.valid == (False, False, True, False, True, False, True, False, True, False)
    assert trace.payloads[2] == {SIGNAL: 10}
    # Source and target both activate at t=4: all reactions see pre-state, so 40 is not visible yet.
    assert trace.payloads[4] == {SIGNAL: 10}
    assert trace.payloads[6] == {SIGNAL: 40}
    assert trace.payloads[8] == {SIGNAL: 70}
    assert result.final_state == {"hold0": {SIGNAL: 70}}


def test_hold_into_target_before_first_source_observes_empty_initial_state() -> None:
    circuit = Circuit("hold_initial")
    source = circuit.signal_event("source", guaranteed_min_separation=2)
    target = circuit.event("target", guaranteed_min_separation=2)
    held = circuit.hold_into(source, target)

    result = simulate_events(
        circuit.build(),
        (),
        (
            EventSchedule(source, (EventOccurrence(4, {SIGNAL: 9}),)),
            EventSchedule(target, (EventOccurrence(1, 1), EventOccurrence(6, 1))),
        ),
        stop_timestamp=8,
    )
    trace = materialize_event_trace(result, held, EventMaterializationPolicy.VALID)

    assert trace.payloads[1] == {}
    assert trace.payloads[6] == {SIGNAL: 9}


def test_hold_into_can_consume_event_merge_and_target_gate_clock() -> None:
    circuit = Circuit("hold_derived")
    left = circuit.signal_event("left", guaranteed_min_separation=2)
    right = circuit.signal_event("right", guaranteed_min_separation=2)
    tick = circuit.event("tick", guaranteed_min_separation=2)
    enabled = circuit.input("enabled")
    merged = circuit.event_merge(left, right)
    active = circuit.gate_clock(tick, when=circuit.sample_on(enabled, tick))
    held = circuit.hold_into(merged, active)

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
    trace = materialize_event_trace(result, held, EventMaterializationPolicy.VALID)

    assert trace.payloads[2] == {SIGNAL: 2}
    assert trace.payloads[4] == {SIGNAL: 5}


def test_hold_into_rejects_level_vector_and_scalar_event_sources() -> None:
    circuit = Circuit("hold_shape")
    level = circuit.signals("level")
    scalar = circuit.event("scalar", guaranteed_min_separation=2)
    target = circuit.event("target", guaranteed_min_separation=2)

    with pytest.raises(EventCrossingError, match="source must be an Event expression"):
        circuit.hold_into(level, target)
    with pytest.raises(EventCrossingError, match="requires a vector Event source"):
        circuit.hold_into(scalar, target)  # type: ignore[arg-type]
