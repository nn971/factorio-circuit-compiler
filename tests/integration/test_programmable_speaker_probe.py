from __future__ import annotations

from examples.programmable_speaker_probe import build_programmable_speaker_probe_blueprint


def _entities(blueprint):
    return blueprint["entities"]


def test_compiled_level_output_composes_with_programmable_speaker() -> None:
    blueprint = build_programmable_speaker_probe_blueprint()
    entities = _entities(blueprint)
    speakers = [entity for entity in entities if entity["name"] == "programmable-speaker"]
    assert len(speakers) == 1
    speaker = speakers[0]

    assert speaker["parameters"] == {
        "playback_volume": 1.0,
        "playback_mode": "local",
        "allow_polyphony": False,
        "volume_controlled_by_signal": False,
        "volume_signal_id": {"type": "virtual", "name": "signal-A"},
    }
    assert speaker["control_behavior"]["circuit_condition"] == {
        "first_signal": {"type": "virtual", "name": "signal-A"},
        "constant": 0,
        "comparator": ">",
    }
    assert speaker["control_behavior"]["circuit_parameters"] == {
        "signal_value_is_pitch": False,
        "stop_playing_sounds": False,
        "instrument_id": 0,
        "note_id": 0,
    }
    assert speaker["alert_parameters"]["show_alert"] is True
    assert speaker["alert_parameters"]["show_on_map"] is True
    assert speaker["alert_parameters"]["alert_message"] == "F1 speaker trigger active"

    shared_docks = [
        entity
        for entity in entities
        if entity["name"] == "constant-combinator"
        and "speaker_trigger_out" in str(entity.get("player_description", ""))
        and "SPEAKER PORT trigger" in str(entity.get("player_description", ""))
    ]
    assert len(shared_docks) == 1
    dock_id = shared_docks[0]["entity_number"]
    speaker_id = speaker["entity_number"]
    wires = {tuple(wire) for wire in blueprint["wires"]}
    assert (dock_id, 2, speaker_id, 2) in wires or (speaker_id, 2, dock_id, 2) in wires

    adapter = next(
        entity
        for entity in entities
        if entity.get("player_description") == "ANCHOR ADAPTER speaker_trigger_out"
    )
    adapter_id = adapter["entity_number"]
    assert any({wire[0], wire[2]} == {adapter_id, dock_id} for wire in blueprint["wires"])
