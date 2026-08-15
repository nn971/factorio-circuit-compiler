from factorio_circuit import Circuit, SignalId, compile_circuit
from factorio_circuit.ir.output import OutputMaterializationPolicy
from factorio_circuit.simulate.physical import simulate_stream

IRON = SignalId("item", "iron-plate")


def test_scalar_event_step_suppresses_occurrences_not_game_ticks() -> None:
    circuit = Circuit("scalar_event_tail_physical")
    event = circuit.event("event", guaranteed_min_separation=1)
    circuit.output("tail", event.step(2), policy=OutputMaterializationPolicy.VALID)

    compiled = compile_circuit(circuit)
    phase = compiled.abstract_physical.outputs[1].phase
    assert compiled.abstract_physical.outputs[0].phase == phase

    rows = []
    occurrences = {0: 10, 3: 20, 4: 30, 8: 40}
    for tick in range(9):
        rows.append(
            {
                "event": occurrences.get(tick, 999),
                "event__valid": int(tick in occurrences),
            }
        )

    trace = simulate_stream(compiled.physical_circuit, rows, flush_ticks=phase)

    # step(2) drops the first two *occurrences* even though they are separated by three game ticks.
    assert trace[4 + phase] == (30, 1)
    assert trace[8 + phase] == (40, 1)
    assert sum(valid for _, valid in trace) == 2


def test_sample_on_step_state_transition_uses_reindexed_event_tail() -> None:
    circuit = Circuit("sample_on_tail_physical")
    data = circuit.signals("data")
    event = circuit.event("event", guaranteed_min_separation=2)
    sampled = circuit.sample_on(data, event).step(1)
    memory = circuit.freeze("memory")
    memory.set(sampled, when=1)
    circuit.output("memory", memory.sample())

    compiled = compile_circuit(circuit)

    rows = []
    occurrences = {0, 3, 7}
    for tick in range(8):
        rows.append(
            {
                "data": {IRON: 100 + tick},
                "event": 1 if tick in occurrences else 0,
                "event__valid": int(tick in occurrences),
            }
        )

    trace = simulate_stream(compiled.physical_circuit, rows, flush_ticks=6)
    changes: list[dict[SignalId, int]] = []
    for (value,) in trace:
        if value and (not changes or value != changes[-1]):
            changes.append(value)

    # The t=0 occurrence is ignored.  Later commits preserve the Level snapshots taken at the
    # surviving semantic occurrences, not values from the physical ticks where the delayed valid
    # tokens finally reach the state cell.
    assert changes == [{IRON: 103}, {IRON: 107}]
