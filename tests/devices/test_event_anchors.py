from __future__ import annotations

import pytest

from factorio_circuit import Circuit, compile_circuit
from factorio_circuit.devices.anchors import AnchorSpec
from factorio_circuit.devices.event_anchors import (
    CompiledEventAnchorBinding,
    compiled_event_inputs_as_anchored_blueprint,
)
from factorio_circuit.devices.protocol import DevicePortDirection
from factorio_circuit.devices.pulse_readers import EVENT_VALID_SIGNAL
from factorio_circuit.ir.physical import WireColor
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality


def _event_result():
    circuit = Circuit("event_anchor_test")
    transfers = circuit.signal_event("transfers", guaranteed_min_separation=1)
    total = circuit.accumulator("total")
    total.add(transfers + circuit.constant_signals({}))
    circuit.output("total", total.sample())
    return compile_circuit(circuit)


def _binding(event: str = "transfers", shape: PayloadShape = PayloadShape.VECTOR):
    return CompiledEventAnchorBinding(
        event,
        AnchorSpec(
            "payload",
            DevicePortDirection.INPUT,
            shape,
            TemporalModality.EVENT,
            WireColor.RED,
            EVENT_VALID_SIGNAL if shape is PayloadShape.SCALAR else None,
        ),
        (-20.0, 0.0),
        AnchorSpec(
            "valid",
            DevicePortDirection.INPUT,
            PayloadShape.SCALAR,
            TemporalModality.EVENT,
            WireColor.GREEN,
            EVENT_VALID_SIGNAL,
        ),
        (-20.0, 2.0),
    )


def test_compiled_event_anchor_restores_event_modality_after_physical_adaptation() -> None:
    anchored = compiled_event_inputs_as_anchored_blueprint(_event_result(), (_binding(),))

    payload = anchored.anchor("payload")
    valid = anchored.anchor("valid")
    assert payload.spec.modality is TemporalModality.EVENT
    assert payload.spec.payload_shape is PayloadShape.VECTOR
    assert payload.position == (-20.0, 0.0)
    assert valid.spec.modality is TemporalModality.EVENT
    assert valid.spec.payload_shape is PayloadShape.SCALAR
    assert valid.spec.signal == EVENT_VALID_SIGNAL
    assert valid.position == (-20.0, 2.0)


def test_compiled_event_anchor_rejects_non_event_physical_input() -> None:
    circuit = Circuit("level_not_event")
    x = circuit.input("x")
    circuit.output("out", x + 1)
    result = compile_circuit(circuit)

    with pytest.raises(ValueError, match="no semantic Event input"):
        compiled_event_inputs_as_anchored_blueprint(
            result,
            (_binding(event="x", shape=PayloadShape.SCALAR),),
        )


def test_compiled_event_anchor_rejects_wrong_payload_shape() -> None:
    with pytest.raises(ValueError, match="carries vector payload"):
        compiled_event_inputs_as_anchored_blueprint(
            _event_result(),
            (_binding(shape=PayloadShape.SCALAR),),
        )
