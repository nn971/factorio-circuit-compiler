from __future__ import annotations

import pytest

from factorio_circuit.devices._blueprint import decode_blueprint
from factorio_circuit.devices.programmable_speaker import (
    PROGRAMMABLE_SPEAKER_PROTOCOL,
    SPEAKER_TRIGGER_SIGNAL,
    ProgrammableSpeakerDevice,
    generate_programmable_speaker_blueprint_string,
)
from factorio_circuit.devices.protocol import DevicePortDirection
from factorio_circuit.ir.physical import WireColor
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality


def _entity(blueprint: dict[str, object], entity_number: int) -> dict[str, object]:
    entities = blueprint["entities"]
    assert isinstance(entities, list)
    return next(
        entity
        for entity in entities
        if isinstance(entity, dict) and entity.get("entity_number") == entity_number
    )


def test_programmable_speaker_protocol_and_endpoint_are_stable() -> None:
    device = ProgrammableSpeakerDevice().build()
    port = device.port("trigger")

    assert device.protocol is PROGRAMMABLE_SPEAKER_PROTOCOL
    assert port.spec.direction is DevicePortDirection.INPUT
    assert port.spec.payload_shape is PayloadShape.SCALAR
    assert port.spec.modality is TemporalModality.LEVEL
    assert port.spec.wire is WireColor.GREEN
    assert port.spec.signal == SPEAKER_TRIGGER_SIGNAL
    assert port.endpoint.entity_number == 1
    assert port.endpoint.connector_id == 2
    assert port.endpoint.position == (0.5, 0.5)

    anchored = device.anchored()
    anchor = anchored.anchor("trigger")
    assert anchor.entity_number == 1
    assert anchor.connector_id == 2
    assert anchor.position == (0.5, 0.5)


def test_programmable_speaker_blueprint_preserves_configuration() -> None:
    blueprint = (
        ProgrammableSpeakerDevice(
            label="Factory alarm",
            playback_volume=0.75,
            playback_mode="surface",
            allow_polyphony=True,
            volume_controlled_by_signal=True,
            signal_value_is_pitch=True,
            stop_playing_sounds=True,
            instrument_id=3,
            note_id=6,
            show_alert=True,
            show_on_map=True,
            alert_message="Production stalled",
        )
        .build()
        .blueprint
    )

    assert blueprint["label"] == "Factory alarm"
    assert blueprint["wires"] == [[1, 2, 2, 2]]
    dock = _entity(blueprint, 1)
    speaker = _entity(blueprint, 2)
    assert dock["name"] == "constant-combinator"
    assert speaker["name"] == "programmable-speaker"
    assert speaker["parameters"] == {
        "playback_volume": 0.75,
        "playback_mode": "surface",
        "allow_polyphony": True,
        "volume_controlled_by_signal": True,
        "volume_signal_id": {"type": "virtual", "name": "signal-A"},
    }
    assert speaker["alert_parameters"] == {
        "show_alert": True,
        "show_on_map": True,
        "icon_signal_id": {"type": "virtual", "name": "signal-A"},
        "alert_message": "Production stalled",
    }
    assert speaker["control_behavior"] == {
        "circuit_condition": {
            "first_signal": {"type": "virtual", "name": "signal-A"},
            "constant": 0,
            "comparator": ">",
        },
        "circuit_parameters": {
            "signal_value_is_pitch": True,
            "stop_playing_sounds": True,
            "instrument_id": 3,
            "note_id": 6,
        },
    }


def test_programmable_speaker_blueprint_string_round_trips() -> None:
    decoded = decode_blueprint(generate_programmable_speaker_blueprint_string())
    assert _entity(decoded, 2)["name"] == "programmable-speaker"


@pytest.mark.parametrize("volume", [-0.01, 1.01])
def test_programmable_speaker_rejects_invalid_volume(volume: float) -> None:
    with pytest.raises(ValueError, match="playback_volume"):
        ProgrammableSpeakerDevice(playback_volume=volume)


def test_programmable_speaker_rejects_invalid_playback_mode() -> None:
    with pytest.raises(ValueError, match="playback mode"):
        ProgrammableSpeakerDevice(playback_mode="universe")  # type: ignore[arg-type]


def test_programmable_speaker_does_not_invent_alert_message_limit() -> None:
    message = "x" * 500
    speaker = ProgrammableSpeakerDevice(alert_message=message).build().blueprint
    assert _entity(speaker, 2)["alert_parameters"]["alert_message"] == message
