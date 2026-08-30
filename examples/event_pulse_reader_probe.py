"""Compile a real semantic Event consumer and attach a transport-belt pulse reader."""

from __future__ import annotations

from dataclasses import dataclass

from factorio_circuit import Circuit, CompilationResult, compile_circuit
from factorio_circuit.devices._blueprint import encode_blueprint
from factorio_circuit.devices.anchors import (
    AnchorBinding,
    AnchorSpec,
    ComposedAnchoredBlueprint,
    compose_anchored_blueprints,
)
from factorio_circuit.devices.event_anchors import (
    CompiledEventAnchorBinding,
    compiled_event_inputs_as_anchored_blueprint,
)
from factorio_circuit.devices.protocol import DevicePortDirection
from factorio_circuit.devices.pulse_readers import (
    EVENT_VALID_SIGNAL,
    TransportBeltPulseReaderDevice,
)
from factorio_circuit.ir.physical import WireColor
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality

PAYLOAD_ANCHOR = (-20.0, 0.0)
VALID_ANCHOR = (-20.0, 2.0)
DEVICE_OFFSET = (-20.5, -0.5)


@dataclass(frozen=True, slots=True)
class EventPulseReaderProbe:
    compiled: CompilationResult
    composed: ComposedAnchoredBlueprint
    blueprint_string: str


def compile_event_pulse_reader_probe() -> EventPulseReaderProbe:
    circuit = Circuit("belt_event_pulse_reader_probe")
    transfers = circuit.signal_event("transfers", guaranteed_min_separation=1)
    total = circuit.accumulator("total")
    total.add(transfers + circuit.constant_signals({}))
    circuit.output("total", total.sample())
    compiled = compile_circuit(circuit)

    compiled_component = compiled_event_inputs_as_anchored_blueprint(
        compiled,
        (
            CompiledEventAnchorBinding(
                "transfers",
                AnchorSpec(
                    "transfers-payload",
                    DevicePortDirection.INPUT,
                    PayloadShape.VECTOR,
                    TemporalModality.EVENT,
                    WireColor.RED,
                ),
                PAYLOAD_ANCHOR,
                AnchorSpec(
                    "transfers-valid",
                    DevicePortDirection.INPUT,
                    PayloadShape.SCALAR,
                    TemporalModality.EVENT,
                    WireColor.GREEN,
                    EVENT_VALID_SIGNAL,
                ),
                VALID_ANCHOR,
            ),
        ),
        label="Compiled Event accumulator",
    )
    reader = TransportBeltPulseReaderDevice().build().anchored()
    composed = compose_anchored_blueprints(
        compiled_component,
        reader,
        bindings=(
            AnchorBinding("transfers-payload", "payload"),
            AnchorBinding("transfers-valid", "valid"),
        ),
        right_offset=DEVICE_OFFSET,
        label="Belt pulse -> compiled Event accumulator",
    )
    return EventPulseReaderProbe(
        compiled,
        composed,
        encode_blueprint(composed.blueprint),
    )


def main() -> None:
    print(compile_event_pulse_reader_probe().blueprint_string)


if __name__ == "__main__":
    main()
