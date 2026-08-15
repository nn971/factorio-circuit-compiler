from factorio_circuit import Circuit, SignalId, compile_circuit, simulate_events
from factorio_circuit.simulate.events import EventOccurrence, EventSchedule
from factorio_circuit.simulate.physical import simulate_stream

IRON = SignalId("item", "iron-plate")
COPPER = SignalId("item", "copper-plate")


def test_direct_event_accumulator_absorbs_payload_without_sum_into_bridge() -> None:
    circuit = Circuit("direct_event_accumulator_physical")
    source = circuit.signal_event("source", guaranteed_min_separation=1)
    total = circuit.accumulator("total")
    total.add(source + circuit.constant_signals({}))
    circuit.output("total", total.sample())

    module = circuit.build()
    reference = simulate_events(
        module,
        [],
        [
            EventSchedule(
                source,
                (
                    EventOccurrence(0, {IRON: 2}),
                    EventOccurrence(2, {COPPER: 3}),
                    EventOccurrence(3, {IRON: 5}),
                ),
            )
        ],
    )
    assert reference.final_state["total"] == {IRON: 7, COPPER: 3}

    compiled = compile_circuit(circuit)
    trace = simulate_stream(
        compiled.physical_circuit,
        [
            {"source": {IRON: 2}, "source__valid": 1},
            {"source": {IRON: 99}, "source__valid": 0},
            {"source": {COPPER: 3}, "source__valid": 1},
            {"source": {IRON: 5}, "source__valid": 1},
        ],
        flush_ticks=4,
    )

    assert trace[-1] == ({IRON: 7, COPPER: 3},)
    descriptions = [
        getattr(entity, "description", "") for entity in compiled.abstract_physical.entities
    ]
    assert (
        sum("Event Accumulator total: add gated occurrence" in item for item in descriptions) == 1
    )
    assert all("SumInto" not in item for item in descriptions)
