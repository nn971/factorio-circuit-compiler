from __future__ import annotations

from examples.event_pulse_reader_probe import (
    DEVICE_OFFSET,
    PAYLOAD_ANCHOR,
    VALID_ANCHOR,
    compile_event_pulse_reader_probe,
)


def _entity(blueprint: dict[str, object], *, description: str) -> dict[str, object]:
    entities = blueprint["entities"]
    assert isinstance(entities, list)
    return next(
        entity
        for entity in entities
        if isinstance(entity, dict) and description in str(entity.get("player_description", ""))
    )


def test_transport_belt_pulse_reader_composes_with_real_semantic_event_input() -> None:
    probe = compile_event_pulse_reader_probe()
    blueprint = probe.composed.blueprint

    assert probe.composed.anchors == ()
    assert probe.blueprint_string.startswith("0")
    assert [port.name for port in probe.compiled.physical_circuit.inputs] == [
        "transfers",
        "transfers__valid",
    ]

    belt = _entity(blueprint, description="BELT PULSE physical transfer sensor")
    payload_delay = _entity(blueprint, description="BELT PULSE align payload by one tick")
    valid_gate = _entity(blueprint, description="BELT PULSE derive aligned valid token")
    assert belt["name"] == "transport-belt"
    assert belt["position"] == {
        "x": 4.5 + DEVICE_OFFSET[0],
        "y": 1.5 + DEVICE_OFFSET[1],
    }
    assert belt["control_behavior"] == {
        "output_networks": {"red": True, "green": True},
        "circuit_read_hand_contents": True,
        "circuit_contents_read_mode": 0,
    }
    assert payload_delay["name"] == "arithmetic-combinator"
    assert valid_gate["name"] == "decider-combinator"

    payload_anchor = _entity(blueprint, description="BELT PULSE PORT payload")
    valid_anchor = _entity(blueprint, description="BELT PULSE PORT valid")
    assert payload_anchor["position"] == {"x": PAYLOAD_ANCHOR[0], "y": PAYLOAD_ANCHOR[1]}
    assert valid_anchor["position"] == {"x": VALID_ANCHOR[0], "y": VALID_ANCHOR[1]}

    descriptions = [
        str(entity.get("player_description", ""))
        for entity in blueprint["entities"]
        if isinstance(entity, dict)
    ]
    assert any("ANCHOR ADAPTER transfers-payload" in item for item in descriptions)
    assert any("ANCHOR ADAPTER transfers-valid" in item for item in descriptions)
