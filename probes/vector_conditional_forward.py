"""Probe Factorio 2.x decider whole-vector forwarding with per-network selection."""

from __future__ import annotations

from probes.blueprint_utils import (
    CIRCUIT_GREEN,
    CIRCUIT_RED,
    COMBINATOR_OUTPUT_RED,
    blueprint,
    constant_combinator,
    print_blueprint,
    signal,
)


def build_blueprint() -> dict[str, object]:
    entities = [
        constant_combinator(
            1,
            -2,
            0,
            [
                ("item", "iron-plate", 11),
                ("item", "copper-plate", 7),
                ("virtual", "signal-A", 5),
            ],
            description="DATA: red network; expected forwarded counts are 11, 7, and 5",
        ),
        constant_combinator(
            2,
            -2,
            2,
            [("virtual", "signal-T", 1)],
            description="CONTROL: green network; signal-T enables forwarding",
        ),
        {
            "entity_number": 3,
            "name": "decider-combinator",
            "position": {"x": 0, "y": 1},
            "direction": 4,
            "player_description": (
                "PROBE: condition reads T from green only; Everything copies counts from red only"
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
                            "signal": signal("virtual", "signal-everything"),
                            "copy_count_from_input": True,
                            "networks": {"red": True, "green": False},
                        }
                    ],
                }
            },
        },
        {
            "entity_number": 4,
            "name": "medium-electric-pole",
            "position": {"x": 3, "y": 1},
        },
    ]
    wires = [
        [1, CIRCUIT_RED, 3, CIRCUIT_RED],
        [2, CIRCUIT_GREEN, 3, CIRCUIT_GREEN],
        [3, COMBINATOR_OUTPUT_RED, 4, CIRCUIT_RED],
    ]
    return blueprint(
        "Probe 2 - vector conditional forwarding",
        (
            "Open the decider or inspect the red output network. Expected output: iron-plate=11, "
            "copper-plate=7, signal-A=5, with no signal-T."
        ),
        entities,
        wires,
    )


if __name__ == "__main__":
    print_blueprint(build_blueprint())
