"""Reusable programmable-speaker alarm output device.

The F1 device deliberately exposes a Level input rather than pretending the current physical-device
boundary can consume Event values. While ``trigger`` is positive, the speaker's configured circuit
condition is true and Factorio plays the selected sound according to the speaker's own playback
rules.

A constant-combinator dock owns the typed external port. The dock is wired to the actual speaker and
can therefore participate in the existing exact-overlap anchoring ABI without making the speaker
prototype itself a special kind of anchor entity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from factorio_circuit.devices._blueprint import Blueprint, encode_blueprint
from factorio_circuit.devices.protocol import (
    BoundDevicePort,
    DeviceEndpoint,
    DevicePortDirection,
    DevicePortSpec,
    DeviceProtocol,
    ExternalDeviceBlueprint,
)
from factorio_circuit.ir.physical import SignalId, WireColor
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality

FACTORIO_BLUEPRINT_VERSION: Final = 562954249306113
SPEAKER_TRIGGER_SIGNAL: Final = SignalId("virtual", "signal-A")
GREEN_CONNECTOR: Final = 2

PROGRAMMABLE_SPEAKER_PROTOCOL: Final = DeviceProtocol(
    "programmable-speaker-v1",
    (
        DevicePortSpec(
            "trigger",
            DevicePortDirection.INPUT,
            PayloadShape.SCALAR,
            TemporalModality.LEVEL,
            WireColor.GREEN,
            SPEAKER_TRIGGER_SIGNAL,
        ),
    ),
)

type SpeakerPlaybackMode = Literal["local", "surface", "global"]


def _signal_json(signal: SignalId) -> dict[str, str]:
    return {"type": signal.kind, "name": signal.name}


@dataclass(frozen=True, slots=True)
class ProgrammableSpeakerDevice:
    """Build one stable speaker with a typed ``trigger > 0`` input.

    Factorio stores the selected instrument and note as numeric indices in the blueprint. F1 keeps
    those ids explicit rather than assigning semantic names that can become stale when the game's
    speaker catalogue changes.
    """

    label: str = "Programmable speaker alarm"
    playback_volume: float = 1.0
    playback_mode: SpeakerPlaybackMode = "local"
    allow_polyphony: bool = False
    volume_controlled_by_signal: bool = False
    signal_value_is_pitch: bool = False
    stop_playing_sounds: bool = False
    instrument_id: int = 0
    note_id: int = 0
    show_alert: bool = False
    show_on_map: bool = False
    alert_message: str = ""

    def __post_init__(self) -> None:
        if not self.label:
            raise ValueError("programmable speaker label must be non-empty")
        if not 0.0 <= self.playback_volume <= 1.0:
            raise ValueError("programmable speaker playback_volume must be within [0, 1]")
        if self.playback_mode not in {"local", "surface", "global"}:
            raise ValueError(
                f"unsupported programmable speaker playback mode {self.playback_mode!r}"
            )
        if self.instrument_id < 0 or self.note_id < 0:
            raise ValueError("programmable speaker instrument/note ids must be non-negative")

    def build(self) -> ExternalDeviceBlueprint:
        blueprint = build_programmable_speaker_blueprint(
            label=self.label,
            playback_volume=self.playback_volume,
            playback_mode=self.playback_mode,
            allow_polyphony=self.allow_polyphony,
            volume_controlled_by_signal=self.volume_controlled_by_signal,
            signal_value_is_pitch=self.signal_value_is_pitch,
            stop_playing_sounds=self.stop_playing_sounds,
            instrument_id=self.instrument_id,
            note_id=self.note_id,
            show_alert=self.show_alert,
            show_on_map=self.show_on_map,
            alert_message=self.alert_message,
        )
        return ExternalDeviceBlueprint(
            PROGRAMMABLE_SPEAKER_PROTOCOL,
            blueprint,
            (
                BoundDevicePort(
                    PROGRAMMABLE_SPEAKER_PROTOCOL.port("trigger"),
                    DeviceEndpoint(1, GREEN_CONNECTOR, WireColor.GREEN, (0.5, 0.5)),
                ),
            ),
        )


def build_programmable_speaker_blueprint(
    *,
    label: str = "Programmable speaker alarm",
    playback_volume: float = 1.0,
    playback_mode: SpeakerPlaybackMode = "local",
    allow_polyphony: bool = False,
    volume_controlled_by_signal: bool = False,
    signal_value_is_pitch: bool = False,
    stop_playing_sounds: bool = False,
    instrument_id: int = 0,
    note_id: int = 0,
    show_alert: bool = False,
    show_on_map: bool = False,
    alert_message: str = "",
) -> Blueprint:
    """Build the two-entity dock + speaker blueprint implementing the F1 protocol."""

    # Keep validation in one place even when callers prefer the low-level blueprint helper.
    config = ProgrammableSpeakerDevice(
        label=label,
        playback_volume=playback_volume,
        playback_mode=playback_mode,
        allow_polyphony=allow_polyphony,
        volume_controlled_by_signal=volume_controlled_by_signal,
        signal_value_is_pitch=signal_value_is_pitch,
        stop_playing_sounds=stop_playing_sounds,
        instrument_id=instrument_id,
        note_id=note_id,
        show_alert=show_alert,
        show_on_map=show_on_map,
        alert_message=alert_message,
    )
    signal = _signal_json(SPEAKER_TRIGGER_SIGNAL)
    return {
        "item": "blueprint",
        "label": config.label,
        "version": FACTORIO_BLUEPRINT_VERSION,
        "icons": [{"signal": {"name": "programmable-speaker"}, "index": 1}],
        "entities": [
            {
                "entity_number": 1,
                "name": "constant-combinator",
                "position": {"x": 0.5, "y": 0.5},
                "player_description": (
                    "SPEAKER PORT trigger — INPUT Level scalar signal-A; GREEN; play while > 0"
                ),
            },
            {
                "entity_number": 2,
                "name": "programmable-speaker",
                "position": {"x": 2.5, "y": 0.5},
                "player_description": "PROGRAMMABLE SPEAKER alarm output",
                "parameters": {
                    "playback_volume": config.playback_volume,
                    "playback_mode": config.playback_mode,
                    "allow_polyphony": config.allow_polyphony,
                    "volume_controlled_by_signal": config.volume_controlled_by_signal,
                    "volume_signal_id": signal,
                },
                "alert_parameters": {
                    "show_alert": config.show_alert,
                    "show_on_map": config.show_on_map,
                    "icon_signal_id": signal,
                    "alert_message": config.alert_message,
                },
                "control_behavior": {
                    "circuit_condition": {
                        "first_signal": signal,
                        "constant": 0,
                        "comparator": ">",
                    },
                    "circuit_parameters": {
                        "signal_value_is_pitch": config.signal_value_is_pitch,
                        "stop_playing_sounds": config.stop_playing_sounds,
                        "instrument_id": config.instrument_id,
                        "note_id": config.note_id,
                    },
                },
            },
        ],
        "wires": [[1, GREEN_CONNECTOR, 2, GREEN_CONNECTOR]],
    }


def generate_programmable_speaker_blueprint_string(
    *,
    label: str = "Programmable speaker alarm",
    playback_volume: float = 1.0,
    playback_mode: SpeakerPlaybackMode = "local",
    allow_polyphony: bool = False,
    volume_controlled_by_signal: bool = False,
    signal_value_is_pitch: bool = False,
    stop_playing_sounds: bool = False,
    instrument_id: int = 0,
    note_id: int = 0,
    show_alert: bool = False,
    show_on_map: bool = False,
    alert_message: str = "",
) -> str:
    """Return an importable Factorio blueprint string for the speaker device."""

    return encode_blueprint(
        build_programmable_speaker_blueprint(
            label=label,
            playback_volume=playback_volume,
            playback_mode=playback_mode,
            allow_polyphony=allow_polyphony,
            volume_controlled_by_signal=volume_controlled_by_signal,
            signal_value_is_pitch=signal_value_is_pitch,
            stop_playing_sounds=stop_playing_sounds,
            instrument_id=instrument_id,
            note_id=note_id,
            show_alert=show_alert,
            show_on_map=show_on_map,
            alert_message=alert_message,
        )
    )


def main() -> None:
    print(generate_programmable_speaker_blueprint_string())


__all__ = [
    "PROGRAMMABLE_SPEAKER_PROTOCOL",
    "SPEAKER_TRIGGER_SIGNAL",
    "ProgrammableSpeakerDevice",
    "SpeakerPlaybackMode",
    "build_programmable_speaker_blueprint",
    "generate_programmable_speaker_blueprint_string",
]


if __name__ == "__main__":
    main()
