"""Small clock-aware Event examples: presence, sampling, and occurrence reindexing."""

from __future__ import annotations

import argparse

from examples._clocked_harness import emit_in_game_example
from factorio_circuit import Circuit
from factorio_circuit.ir.output import OutputMaterializationPolicy


def build_pulse_echo() -> Circuit:
    circuit = Circuit("event_pulse_echo")
    pulse = circuit.event("pulse", guaranteed_min_separation=20)
    circuit.output("echo", pulse, policy=OutputMaterializationPolicy.VALID)
    return circuit


def build_triggered_sampler() -> Circuit:
    circuit = Circuit("triggered_sampler")
    value = circuit.input("value")
    trigger = circuit.event("trigger", guaranteed_min_separation=20)
    circuit.output(
        "sampled",
        circuit.sample_on(value, trigger),
        policy=OutputMaterializationPolicy.VALID,
    )
    return circuit


def build_occurrence_step() -> Circuit:
    circuit = Circuit("occurrence_step")
    source = circuit.event("source", guaranteed_min_separation=20)
    circuit.output("now", source.step(0), policy=OutputMaterializationPolicy.VALID)
    circuit.output("tail", source.step(1), policy=OutputMaterializationPolicy.VALID)
    return circuit


CASES = {
    "pulse_echo": (
        build_pulse_echo,
        {
            "pulse": {20: 7, 70: 0, 120: 3},
            "pulse__valid": {20: 1, 70: 1, 120: 1},
        },
        160,
        (
            "echo occurrences carry 7, 0, 3 at driver phases 20, 70, 120",
            "echo__valid pulses at all three phases, including the zero-payload occurrence",
        ),
    ),
    "sample_on": (
        build_triggered_sampler,
        {
            "value": {20: 10, 70: 30, 120: 40},
            "trigger__valid": {20: 1, 70: 1, 120: 1},
        },
        160,
        (
            "sampled occurrences carry 10, 30, 40",
            "changing value without trigger would produce no sampled occurrence",
        ),
    ),
    "occurrence_step": (
        build_occurrence_step,
        {
            "source": {20: 1, 60: 2, 110: 3, 170: 4},
            "source__valid": {20: 1, 60: 1, 110: 1, 170: 1},
        },
        200,
        (
            "now emits 1, 2, 3, 4",
            "tail suppresses the first occurrence and emits 2, 3, 4 at the later occurrences",
            "unequal physical gaps do not change .step(1): it counts occurrences, not game ticks",
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
        title=f"Clocked basics: {args.case}",
        expected=expected,
    )


if __name__ == "__main__":
    main()
