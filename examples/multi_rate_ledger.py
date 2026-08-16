"""Capstone clock-aware example: multi-rate production telemetry."""

from __future__ import annotations

from examples._clocked_harness import DriverSchedule, emit_in_game_example
from factorio_circuit import Circuit, SignalId
from factorio_circuit.ir.output import OutputMaterializationPolicy

IRON = SignalId("item", "iron-plate")
COPPER = SignalId("item", "copper-plate")

SCHEDULE: DriverSchedule = {
    "enabled": {70: 1, 230: 1},
    "worker0": {20: {IRON: 2}, 80: {IRON: 3}, 140: {IRON: 5}},
    "worker0__valid": {20: 1, 80: 1, 140: 1},
    "worker1": {40: {COPPER: 4}, 80: {IRON: 1}, 160: {COPPER: 6}},
    "worker1__valid": {40: 1, 80: 1, 160: 1},
    "worker2": {60: {IRON: 7}, 120: {COPPER: 2}},
    "worker2__valid": {60: 1, 120: 1},
    "fast_tick__valid": {50: 1, 110: 1, 170: 1, 230: 1},
    "slow_tick__valid": {70: 1, 160: 1, 230: 1},
    "audit_tick__valid": {230: 1},
}
PERIOD = 280
EXPECTED = (
    "fast@50 = iron=2,copper=4; fast@110 = iron=11",
    "fast@170 = iron=5,copper=8; fast@230 is empty",
    "slow@70 = iron=9,copper=4; slow_tick@160 is gated away",
    "slow@230 = iron=9,copper=8 because the gated-away tick did not close the window",
    "audit@230 = iron=18,copper=12",
    "lifetime reaches iron=18,copper=12 in the first cycle and keeps accumulating",
)


def build_multi_rate_ledger() -> Circuit:
    circuit = Circuit("multi_rate_ledger")
    enabled = circuit.input("enabled")
    worker0 = circuit.signal_event("worker0", guaranteed_min_separation=5)
    worker1 = circuit.signal_event("worker1", guaranteed_min_separation=4)
    worker2 = circuit.signal_event("worker2", guaranteed_min_separation=5)
    fast_tick = circuit.event("fast_tick", guaranteed_min_separation=4)
    slow_tick = circuit.event("slow_tick", guaranteed_min_separation=5)
    audit_tick = circuit.event("audit_tick", guaranteed_min_separation=1)

    merged = circuit.event_merge(worker0, worker1, worker2)
    slow_report = circuit.gate_clock(
        slow_tick,
        when=circuit.sample_on(enabled, slow_tick),
    )
    fast_sum = circuit.sum_into(merged, fast_tick)
    slow_sum = circuit.sum_into(merged, slow_report)
    audit_sum = circuit.sum_into(merged, audit_tick)

    lifetime = circuit.accumulator("lifetime")
    lifetime.add(merged.step(0))

    circuit.output("fast", fast_sum, policy=OutputMaterializationPolicy.VALID)
    circuit.output("slow", slow_sum, policy=OutputMaterializationPolicy.VALID)
    circuit.output("audit", audit_sum, policy=OutputMaterializationPolicy.VALID)
    circuit.output("lifetime", lifetime.sample())
    return circuit


def main() -> None:
    emit_in_game_example(
        build_multi_rate_ledger(),
        SCHEDULE,
        period=PERIOD,
        title="Multi-rate production ledger",
        expected=EXPECTED,
    )


if __name__ == "__main__":
    main()
