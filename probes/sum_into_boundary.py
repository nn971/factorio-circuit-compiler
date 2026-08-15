"""Probe the simultaneous source/target boundary needed by physical SumInto lowering."""

from __future__ import annotations

from probes.blueprint_utils import (
    CIRCUIT_GREEN,
    CIRCUIT_RED,
    COMBINATOR_OUTPUT_GREEN,
    COMBINATOR_OUTPUT_RED,
    blueprint,
    print_blueprint,
    signal,
)


def arithmetic(
    entity_number: int,
    x: float,
    y: float,
    *,
    first: str,
    second_signal: str | None = None,
    second_constant: int | None = None,
    operation: str,
    output: str,
    description: str,
) -> dict[str, object]:
    conditions: dict[str, object] = {
        "first_signal": signal("virtual", first),
        "first_signal_networks": {"red": True, "green": False},
        "operation": operation,
        "output_signal": signal("virtual", output),
    }
    if second_signal is not None:
        conditions["second_signal"] = signal("virtual", second_signal)
        conditions["second_signal_networks"] = {"red": True, "green": False}
    elif second_constant is not None:
        conditions["second_constant"] = second_constant
    else:
        raise ValueError("arithmetic probe entity needs a second operand")
    return {
        "entity_number": entity_number,
        "name": "arithmetic-combinator",
        "position": {"x": x, "y": y},
        "direction": 4,
        "player_description": description,
        "control_behavior": {"arithmetic_conditions": conditions},
    }


def pulse_decider(
    entity_number: int,
    x: float,
    y: float,
    *,
    phase: int,
    output_signal: str,
    output_constant: int,
    description: str,
) -> dict[str, object]:
    return {
        "entity_number": entity_number,
        "name": "decider-combinator",
        "position": {"x": x, "y": y},
        "direction": 4,
        "player_description": description,
        "control_behavior": {
            "decider_conditions": {
                "conditions": [
                    {
                        "first_signal": signal("virtual", "signal-P"),
                        "first_signal_networks": {"red": True, "green": False},
                        "comparator": "=",
                        "constant": phase,
                    }
                ],
                "outputs": [
                    {
                        "signal": signal("virtual", output_signal),
                        "copy_count_from_input": False,
                        "constant": output_constant,
                    }
                ],
            }
        },
    }


def build_blueprint() -> dict[str, object]:
    entities = [
        arithmetic(
            1,
            -9,
            0,
            first="signal-C",
            second_constant=1,
            operation="+",
            output="signal-C",
            description="FREE-RUNNING COUNTER: C <- C + 1",
        ),
        arithmetic(
            2,
            -6,
            0,
            first="signal-C",
            second_constant=240,
            operation="%",
            output="signal-P",
            description="PHASE: P = C mod 240",
        ),
        pulse_decider(
            3,
            -3,
            -3,
            phase=20,
            output_signal="signal-A",
            output_constant=5,
            description="SOURCE: preload A=5",
        ),
        pulse_decider(
            4,
            -3,
            -1,
            phase=80,
            output_signal="signal-A",
            output_constant=7,
            description="SOURCE: A=7 simultaneous with first target",
        ),
        pulse_decider(
            5,
            -3,
            1,
            phase=80,
            output_signal="signal-T",
            output_constant=1,
            description="TARGET 1: simultaneous with A=7",
        ),
        pulse_decider(
            6,
            -3,
            3,
            phase=140,
            output_signal="signal-A",
            output_constant=3,
            description="SOURCE: post-clear A=3",
        ),
        pulse_decider(
            7,
            -3,
            5,
            phase=200,
            output_signal="signal-T",
            output_constant=1,
            description="TARGET 2: expected snapshot A=3",
        ),
        {
            "entity_number": 8,
            "name": "decider-combinator",
            "position": {"x": 1, "y": 0},
            "direction": 4,
            "player_description": (
                "MEMORY: while T=0, copy A from red data network back to red data network"
            ),
            "control_behavior": {
                "decider_conditions": {
                    "conditions": [
                        {
                            "first_signal": signal("virtual", "signal-T"),
                            "first_signal_networks": {"red": False, "green": True},
                            "comparator": "=",
                            "constant": 0,
                        }
                    ],
                    "outputs": [
                        {
                            "signal": signal("virtual", "signal-A"),
                            "copy_count_from_input": True,
                            "networks": {"red": True, "green": False},
                        }
                    ],
                }
            },
        },
        {
            "entity_number": 9,
            "name": "decider-combinator",
            "position": {"x": 1, "y": 3},
            "direction": 4,
            "player_description": (
                "SNAPSHOT: when T>0, copy A from red; expected pulses 12 then 3"
            ),
            "control_behavior": {
                "decider_conditions": {
                    "conditions": [
                        {
                            "first_signal": signal("virtual", "signal-T"),
                            "first_signal_networks": {"red": False, "green": True},
                            "comparator": ">",
                            "constant": 0,
                        }
                    ],
                    "outputs": [
                        {
                            "signal": signal("virtual", "signal-A"),
                            "copy_count_from_input": True,
                            "networks": {"red": True, "green": False},
                        }
                    ],
                }
            },
        },
        {
            "entity_number": 10,
            "name": "medium-electric-pole",
            "position": {"x": 4, "y": 4},
        },
        arithmetic(
            11,
            5,
            1,
            first="signal-A",
            second_signal="signal-S",
            operation="+",
            output="signal-S",
            description=("CONVENIENCE HISTORY: S <- S + snapshot A; expect 12,15,27,30,..."),
        ),
        {
            "entity_number": 12,
            "name": "medium-electric-pole",
            "position": {"x": 8, "y": 1},
        },
    ]

    wires = [
        # Counter and phase distribution.
        [1, COMBINATOR_OUTPUT_RED, 1, CIRCUIT_RED],
        [1, COMBINATOR_OUTPUT_RED, 2, CIRCUIT_RED],
        [2, COMBINATOR_OUTPUT_RED, 3, CIRCUIT_RED],
        [2, COMBINATOR_OUTPUT_RED, 4, CIRCUIT_RED],
        [2, COMBINATOR_OUTPUT_RED, 5, CIRCUIT_RED],
        [2, COMBINATOR_OUTPUT_RED, 6, CIRCUIT_RED],
        [2, COMBINATOR_OUTPUT_RED, 7, CIRCUIT_RED],
        # Red data network: source pulses + memory feedback, shared by memory and snapshot.
        [3, COMBINATOR_OUTPUT_RED, 8, CIRCUIT_RED],
        [4, COMBINATOR_OUTPUT_RED, 8, CIRCUIT_RED],
        [6, COMBINATOR_OUTPUT_RED, 8, CIRCUIT_RED],
        [8, COMBINATOR_OUTPUT_RED, 8, CIRCUIT_RED],
        [8, CIRCUIT_RED, 9, CIRCUIT_RED],
        # Green target/control network.
        [5, COMBINATOR_OUTPUT_GREEN, 8, CIRCUIT_GREEN],
        [7, COMBINATOR_OUTPUT_GREEN, 8, CIRCUIT_GREEN],
        [8, CIRCUIT_GREEN, 9, CIRCUIT_GREEN],
        # Snapshot observation and convenience accumulated history.
        [9, COMBINATOR_OUTPUT_RED, 10, CIRCUIT_RED],
        [9, COMBINATOR_OUTPUT_RED, 11, CIRCUIT_RED],
        [11, COMBINATOR_OUTPUT_RED, 11, CIRCUIT_RED],
        [11, COMBINATOR_OUTPUT_RED, 12, CIRCUIT_RED],
    ]
    return blueprint(
        "Probe 1 - SumInto simultaneous boundary",
        (
            "Self-driving scalar SumInto boundary probe. Each 240-tick cycle preloads A=5, then "
            "presents A=7 with T=1, then A=3, then T=1. Snapshot output should pulse A=12 then "
            "A=3. Convenience history S should climb 12,15,27,30,..."
        ),
        entities,
        wires,
    )


if __name__ == "__main__":
    print_blueprint(build_blueprint())
