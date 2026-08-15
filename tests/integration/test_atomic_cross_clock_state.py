from factorio_circuit import Circuit, EventOccurrence, EventSchedule, SignalId, simulate_events

SIGNAL = SignalId("virtual", "signal-test")


def _simultaneous_clear_then_add(*, declare_add_first: bool) -> dict[str, dict[SignalId, int]]:
    circuit = Circuit("cross_clock_commit_order")
    if declare_add_first:
        add_source = circuit.signal_event("add", guaranteed_min_separation=2)
        clear_source = circuit.event("clear", guaranteed_min_separation=2)
    else:
        clear_source = circuit.event("clear", guaranteed_min_separation=2)
        add_source = circuit.signal_event("add", guaranteed_min_separation=2)

    memory = circuit.accumulator("memory")
    # Canonical register transition order is clear first, then add, independently of source order.
    memory.clear(when=clear_source)
    memory.add(add_source * 1)

    result = simulate_events(
        circuit.build(),
        (),
        (
            EventSchedule(add_source, (EventOccurrence(1, {SIGNAL: 7}),)),
            EventSchedule(clear_source, (EventOccurrence(1, 1),)),
        ),
        stop_timestamp=3,
    )
    return result.final_state


def test_simultaneous_cross_clock_commits_follow_transition_order_not_source_order() -> None:
    expected = {"memory": {SIGNAL: 7}}

    assert _simultaneous_clear_then_add(declare_add_first=False) == expected
    assert _simultaneous_clear_then_add(declare_add_first=True) == expected
