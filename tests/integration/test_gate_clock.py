import pytest

from factorio_circuit import (
    Circuit,
    EventCausalityError,
    EventCrossingError,
    EventOccurrence,
    EventSchedule,
    EventScheduleError,
    SignalId,
    simulate_events,
)
from factorio_circuit.ir.clocks import GateClock
from factorio_circuit.ir.semantic import ClockProvenance, PayloadShape

SIGNAL = SignalId("virtual", "signal-test")


def test_gate_clock_is_a_derived_unit_subclock_with_inherited_contract() -> None:
    circuit = Circuit("gate_contract")
    parent = circuit.event("parent", guaranteed_min_separation=4)

    gated = circuit.gate_clock(parent, when=parent > 0)

    assert isinstance(gated.ir, GateClock)
    assert gated.ir.parent == parent.ir
    assert gated.ir.payload_shape is PayloadShape.SCALAR
    assert gated.clock.provenance is ClockProvenance.DERIVED
    assert gated.clock.clock_id != parent.clock.clock_id
    assert gated.clock.contract == parent.clock.contract
    assert gated.clock.guaranteed_min_separation == 4


def test_gate_clock_is_interned_by_parent_and_predicate() -> None:
    circuit = Circuit("gate_cse")
    parent = circuit.event("parent", guaranteed_min_separation=3)
    predicate = parent > 0

    first = circuit.gate_clock(parent, when=predicate)
    second = circuit.gate_clock(parent, when=predicate)
    module = circuit.build()

    assert first.ir is second.ir
    assert module.event_inputs == (parent.ir, first.ir)


def test_gate_clock_can_filter_a_vector_event_payload() -> None:
    circuit = Circuit("gate_vector")
    parent = circuit.signal_event("parent", guaranteed_min_separation=2)

    gated = circuit.gate_clock(parent, when=parent.signal(SIGNAL) > 0)

    assert isinstance(gated.ir, GateClock)
    assert gated.ir.parent == parent.ir
    assert gated.clock.guaranteed_min_separation == 2


def test_gate_clock_requires_explicit_level_sampling_on_parent_clock() -> None:
    circuit = Circuit("gate_explicit_level")
    parent = circuit.event("parent", guaranteed_min_separation=2)
    enabled = circuit.input("enabled")

    with pytest.raises(EventCrossingError, match="cannot implicitly sample a Level"):
        circuit.gate_clock(parent, when=enabled)

    enabled_on_parent = circuit.sample_on(enabled, parent)
    gated = circuit.gate_clock(parent, when=enabled_on_parent)

    assert isinstance(gated.ir, GateClock)


def test_gate_clock_drives_state_only_on_surviving_parent_occurrences() -> None:
    circuit = Circuit("gate_state")
    parent = circuit.event("parent", guaranteed_min_separation=2)
    enabled = circuit.input("enabled")
    data = circuit.signals("data")
    gated = circuit.gate_clock(parent, when=circuit.sample_on(enabled, parent))
    sampled_data = circuit.sample_on(data, gated)
    memory = circuit.freeze("memory")
    memory.set(sampled_data, when=1)

    level_stream = [
        {"enabled": 0, "data": {SIGNAL: 10}},
        {"enabled": 0, "data": {SIGNAL: 11}},
        {"enabled": 0, "data": {SIGNAL: 12}},
        {"enabled": 1, "data": {SIGNAL: 13}},
        {"enabled": 0, "data": {SIGNAL: 14}},
        {"enabled": 0, "data": {SIGNAL: 15}},
        {"enabled": 0, "data": {SIGNAL: 16}},
        {"enabled": 1, "data": {SIGNAL: 17}},
    ]
    schedule = EventSchedule(
        parent,
        (
            EventOccurrence(1, -4),
            EventOccurrence(3, 8),
            EventOccurrence(5, 2),
            EventOccurrence(7, 9),
        ),
    )

    result = simulate_events(circuit.build(), level_stream, (schedule,), stop_timestamp=8)

    assert result.final_state == {"memory": {SIGNAL: 17}}
    activations = {
        reaction.timestamp: tuple(activation.source.name for activation in reaction.activations)
        for reaction in result.reactions
    }
    assert activations == {
        1: ("parent",),
        3: ("parent", gated.name),
        5: ("parent",),
        7: ("parent", gated.name),
    }


def test_gate_clock_schedules_cannot_be_supplied_by_the_caller() -> None:
    circuit = Circuit("gate_schedule")
    parent = circuit.event("parent", guaranteed_min_separation=2)
    gated = circuit.gate_clock(parent, when=parent > 0)
    module = circuit.build()

    with pytest.raises(EventScheduleError, match="cannot be supplied externally"):
        simulate_events(
            module,
            (),
            (
                EventSchedule(parent, ()),
                EventSchedule(gated, (EventOccurrence(1, 1),)),
            ),
            stop_timestamp=2,
        )


def test_gate_clock_can_be_chained_and_preserves_the_parent_bound() -> None:
    circuit = Circuit("gate_chain")
    parent = circuit.event("parent", guaranteed_min_separation=5)
    first = circuit.gate_clock(parent, when=parent > 0)
    second = circuit.gate_clock(first, when=first != 0)

    assert second.ir.parent == first.ir
    assert second.clock.guaranteed_min_separation == 5


def test_gate_clock_rejects_state_dependent_predicates() -> None:
    circuit = Circuit("gate_state_predicate")
    parent = circuit.event("parent", guaranteed_min_separation=2)
    memory = circuit.freeze("memory")
    sampled = circuit.sample_on(memory.sample().signal(SIGNAL), parent)

    with pytest.raises(EventCrossingError, match="state-dependent GateClock predicates"):
        circuit.gate_clock(parent, when=sampled)


def test_gate_clock_rejects_reindexed_parent_lookahead() -> None:
    circuit = Circuit("gate_lookahead")
    parent = circuit.event("parent", guaranteed_min_separation=2)
    future_predicate = (parent > 0).step()

    with pytest.raises(EventCausalityError, match="current parent occurrence"):
        circuit.gate_clock(parent, when=future_predicate)
