"""Event-driven state example: an irregular producer feeds persistent lifetime state."""

from __future__ import annotations

from examples._clocked_harness import DriverSchedule, emit_in_game_example
from factorio_circuit import Circuit, SignalId
from factorio_circuit.ir.output import OutputMaterializationPolicy

IRON = SignalId("item", "iron-plate")
COPPER = SignalId("item", "copper-plate")

SCHEDULE: DriverSchedule = {
    "source": {
        20: {IRON: 2},
        70: {COPPER: 4},
        120: {IRON: 3},
    },
    "source__valid": {20: 1, 70: 1, 120: 1},
}
PERIOD = 180
EXPECTED = (
    "source is sparse, but lifetime is a persistent Level output",
    "after first-cycle events lifetime reaches iron=5, copper=4",
    "the repeating driver adds the same iron=5, copper=4 again each later cycle",
)


def build_event_accumulator() -> Circuit:
    circuit = Circuit("event_accumulator")
    source = circuit.signal_event("source", guaranteed_min_separation=20)
    lifetime = circuit.accumulator("lifetime")
    lifetime.add(source.step(0))

    circuit.output("source", source, policy=OutputMaterializationPolicy.VALID)
    circuit.output("lifetime", lifetime.sample())
    return circuit


def main() -> None:
    emit_in_game_example(
        build_event_accumulator(),
        SCHEDULE,
        period=PERIOD,
        title="Event-driven state: lifetime accumulator",
        expected=EXPECTED,
    )


if __name__ == "__main__":
    main()
