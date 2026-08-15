"""Flow-local Event reindexing in the semantic reference simulator."""

from factorio_circuit import (
    Circuit,
    EventOccurrence,
    EventSchedule,
    SignalId,
    simulate_events,
)

IRON = SignalId("item", "iron-plate")

circuit = Circuit("event_occurrence_step")
crafted = circuit.signal_event("crafted", guaranteed_min_separation=1)
total = circuit.accumulator("total")

# The shifted flow starts at crafted[1], so crafted[0] does not contribute.
total.add(crafted.step())
circuit.output("total", total.sample())


if __name__ == "__main__":
    result = simulate_events(
        circuit.build(),
        [],
        [
            EventSchedule(
                crafted,
                (
                    EventOccurrence(2, {IRON: 10}),
                    EventOccurrence(5, {IRON: 20}),
                    EventOccurrence(9, {IRON: 30}),
                ),
            )
        ],
    )
    for reaction in result.reactions:
        print(reaction.timestamp, reaction.state_after["total"])
