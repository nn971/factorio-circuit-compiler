"""F1 probe: compile a Level alarm signal and attach the reusable programmable speaker.

The compiler owns the deterministic logic. ``compiled_module_as_anchored_blueprint`` normalizes
its public output to the speaker ABI's fixed GREEN ``signal-A`` lane, and exact-overlap composition
merges that output anchor with the speaker's input dock.
"""

from __future__ import annotations

from factorio_circuit import Circuit, compile_circuit
from factorio_circuit.devices import (
    SPEAKER_TRIGGER_SIGNAL,
    AnchorBinding,
    AnchorSpec,
    CompiledAnchorBinding,
    ProgrammableSpeakerDevice,
    compiled_module_as_anchored_blueprint,
    compose_anchored_blueprints,
)
from factorio_circuit.devices._blueprint import Blueprint, encode_blueprint
from factorio_circuit.devices.protocol import DevicePortDirection
from factorio_circuit.ir.physical import WireColor
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality


def build_programmable_speaker_probe_blueprint() -> Blueprint:
    circuit = Circuit("programmable_speaker_probe")
    alarm = circuit.input("alarm")
    circuit.output("speaker_trigger", alarm)
    result = compile_circuit(circuit, optimize=False)

    output = next(
        port for port in result.physical_circuit.outputs if port.name == "speaker_trigger"
    )
    output_position = result.layout.positions[output.marker_entity]
    anchor_position = (output_position[0] + 7.0, output_position[1])

    compiled = compiled_module_as_anchored_blueprint(
        result,
        (
            CompiledAnchorBinding(
                "speaker_trigger",
                AnchorSpec(
                    "speaker_trigger_out",
                    DevicePortDirection.OUTPUT,
                    PayloadShape.SCALAR,
                    TemporalModality.LEVEL,
                    WireColor.GREEN,
                    SPEAKER_TRIGGER_SIGNAL,
                ),
                anchor_position,
            ),
        ),
        label="Compiled programmable-speaker probe",
    )
    speaker = (
        ProgrammableSpeakerDevice(
            label="F1 programmable speaker alarm",
            show_alert=True,
            show_on_map=True,
            alert_message="F1 speaker trigger active",
        )
        .build()
        .anchored()
    )
    speaker_anchor = speaker.anchor("trigger")
    offset = (
        anchor_position[0] - speaker_anchor.position[0],
        anchor_position[1] - speaker_anchor.position[1],
    )
    composed = compose_anchored_blueprints(
        compiled,
        speaker,
        bindings=(AnchorBinding("speaker_trigger_out", "trigger"),),
        right_offset=offset,
        label="F1 programmable speaker compiled integration",
    )
    return composed.blueprint


def generate_programmable_speaker_probe_string() -> str:
    return encode_blueprint(build_programmable_speaker_probe_blueprint())


def main() -> None:
    print(generate_programmable_speaker_probe_string())


if __name__ == "__main__":
    main()
