"""Probe a transport-belt pulse as the canonical external Event-clock adapter."""

from __future__ import annotations

from probes.blueprint_utils import (
    CIRCUIT_GREEN,
    CIRCUIT_RED,
    COMBINATOR_OUTPUT_GREEN,
    blueprint,
    print_blueprint,
    signal,
)


def build_blueprint() -> dict[str, object]:
    entities: list[dict[str, object]] = []
    for entity_number, x in enumerate(range(-5, 0), start=1):
        belt: dict[str, object] = {
            "entity_number": entity_number,
            "name": "transport-belt",
            "position": {"x": x, "y": 0},
            "direction": 4,
        }
        if entity_number == 3:
            belt["control_behavior"] = {
                "circuit_read_hand_contents": True,
                "circuit_contents_read_mode": 0,
                "output_networks": {"red": True, "green": False},
            }
        entities.append(belt)

    entities.extend(
        [
            {
                "entity_number": 6,
                "name": "arithmetic-combinator",
                "position": {"x": 1, "y": 0},
                "direction": 4,
                "player_description": "DELAY: Each + 0 -> Each; exactly one combinator tick",
                "control_behavior": {
                    "arithmetic_conditions": {
                        "first_signal": signal("virtual", "signal-each"),
                        "first_signal_networks": {"red": True, "green": False},
                        "second_constant": 0,
                        "operation": "+",
                        "output_signal": signal("virtual", "signal-each"),
                    }
                },
            },
            {
                "entity_number": 7,
                "name": "medium-electric-pole",
                "position": {"x": -2, "y": 2},
            },
            {
                "entity_number": 8,
                "name": "medium-electric-pole",
                "position": {"x": 3, "y": 2},
            },
        ]
    )
    wires = [
        [3, CIRCUIT_RED, 6, CIRCUIT_RED],
        [3, CIRCUIT_RED, 7, CIRCUIT_RED],
        [6, COMBINATOR_OUTPUT_GREEN, 8, CIRCUIT_GREEN],
    ]
    return blueprint(
        "Probe 3 - belt external Event timing",
        (
            "Drop an item onto the left end. The middle read belt is pulse mode. The red pole sees "
            "the raw one-tick item pulse; the green pole sees the same payload one tick later."
        ),
        entities,
        wires,
    )


if __name__ == "__main__":
    print_blueprint(build_blueprint())
