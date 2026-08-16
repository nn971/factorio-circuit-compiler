"""Derived-clock examples: gating and additive Event merge."""

from __future__ import annotations

import argparse

from examples._clocked_harness import emit_in_game_example
from factorio_circuit import Circuit, SignalId
from factorio_circuit.ir.output import OutputMaterializationPolicy

IRON = SignalId("item", "iron-plate")
COPPER = SignalId("item", "copper-plate")


def build_enabled_clock() -> Circuit:
    circuit = Circuit("enabled_clock")
    enabled = circuit.input("enabled")
    tick = circuit.event("tick", guaranteed_min_separation=20)
    gated = circuit.gate_clock(tick, when=circuit.sample_on(enabled, tick))
    circuit.output("enabled_tick", gated, policy=OutputMaterializationPolicy.VALID)
    return circuit


def build_event_merge() -> Circuit:
    circuit = Circuit("event_merge")
    left = circuit.signal_event("left", guaranteed_min_separation=20)
    right = circuit.signal_event("right", guaranteed_min_separation=20)
    merged = circuit.event_merge(left, right)
    circuit.output("merged", merged, policy=OutputMaterializationPolicy.VALID)
    return circuit


CASES = {
    "gate_clock": (
        build_enabled_clock,
        {
            "enabled": {30: 1, 130: 1},
            "tick__valid": {30: 1, 80: 1, 130: 1, 180: 1},
        },
        220,
        (
            "enabled_tick__valid pulses at phases 30 and 130 only",
            "tick occurrences at 80 and 180 are removed from the derived clock",
        ),
    ),
    "event_merge": (
        build_event_merge,
        {
            "left": {20: {IRON: 3}, 80: {IRON: 5}},
            "left__valid": {20: 1, 80: 1},
            "right": {50: {COPPER: 4}, 80: {IRON: 7}, 140: {COPPER: 2}},
            "right__valid": {50: 1, 80: 1, 140: 1},
        },
        200,
        (
            "merged emits iron=3 at phase 20 and copper=4 at phase 50",
            "simultaneous phase-80 parents coalesce into one occurrence with iron=12",
            "merged emits copper=2 at phase 140",
        ),
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=CASES)
    args = parser.parse_args()
    builder, schedule, period, expected = CASES[args.case]
    emit_in_game_example(
        builder(),
        schedule,
        period=period,
        title=f"Derived clocks: {args.case}",
        expected=expected,
    )


if __name__ == "__main__":
    main()
