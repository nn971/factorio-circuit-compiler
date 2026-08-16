"""Stateful cross-clock examples showing HoldInto and SumInto boundary conventions."""

from __future__ import annotations

import argparse

from factorio_circuit import Circuit, SignalId
from factorio_circuit.ir.output import OutputMaterializationPolicy

from examples._clocked_harness import emit_in_game_example

IRON = SignalId("item", "iron-plate")
COPPER = SignalId("item", "copper-plate")


def build_hold_into() -> Circuit:
    circuit = Circuit("hold_into")
    source = circuit.signal_event("source", guaranteed_min_separation=20)
    report = circuit.event("report", guaranteed_min_separation=20)
    latest = circuit.hold_into(source, report)
    circuit.output("latest", latest, policy=OutputMaterializationPolicy.VALID)
    return circuit


def build_sum_into() -> Circuit:
    circuit = Circuit("sum_into")
    source = circuit.signal_event("source", guaranteed_min_separation=20)
    report = circuit.event("report", guaranteed_min_separation=20)
    window = circuit.sum_into(source, report)
    circuit.output("window", window, policy=OutputMaterializationPolicy.VALID)
    return circuit


CASES = {
    "hold_into": (
        build_hold_into,
        {
            "source": {20: {IRON: 5}, 80: {COPPER: 7}, 140: {IRON: 3}},
            "source__valid": {20: 1, 80: 1, 140: 1},
            "report__valid": {80: 1, 120: 1, 180: 1},
        },
        240,
        (
            "report@80 is simultaneous with source copper=7 but sees strict-prior iron=5",
            "report@120 sees copper=7",
            "report@180 sees iron=3",
        ),
    ),
    "sum_into": (
        build_sum_into,
        {
            "source": {
                20: {IRON: 2},
                60: {IRON: 5},
                100: {IRON: 7},
                160: {IRON: 3},
            },
            "source__valid": {20: 1, 60: 1, 100: 1, 160: 1},
            "report__valid": {100: 1, 200: 1},
        },
        240,
        (
            "report@100 emits iron=14: source@100 is included in the current window",
            "report@200 emits iron=3",
            "the bridge integrates the right-closed interval (previous_report, current_report]",
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
        title=f"Clock crossings: {args.case}",
        expected=expected,
    )


if __name__ == "__main__":
    main()
