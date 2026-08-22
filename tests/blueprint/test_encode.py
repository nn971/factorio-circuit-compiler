import base64
import json
import zlib

import pytest

from factorio_circuit import Circuit, SignalId, compile_circuit
from factorio_circuit.blueprint.layout_encode import layout_to_blueprint_json
from factorio_circuit.ir.physical import (
    ArithmeticCombinator,
    ConstantCombinator,
    DeciderCombinator,
    Operand,
    PhysicalCircuit,
)
from factorio_circuit.synthesis.layout import Layout, LayoutRelay


def test_blueprint_string_round_trip() -> None:
    c = Circuit("add_one")
    a = c.input("a")
    c.output("x", a + 1)
    result = compile_circuit(c)

    encoded = result.blueprint_string
    decoded = zlib.decompress(base64.b64decode(encoded[1:]))
    payload = json.loads(decoded)
    assert payload["blueprint"]["label"] == "add_one"


def test_constant_signal_filter_serializes_normal_quality_and_count() -> None:
    c = Circuit("constant_f")
    fib = SignalId("virtual", "signal-F")
    c.output("one", c.constant_signals({fib: 1}))

    result = compile_circuit(c)
    constants = [
        entity
        for entity in result.blueprint_json["blueprint"]["entities"]
        if entity["name"] == "constant-combinator" and entity.get("control_behavior")
    ]
    assert len(constants) == 1

    filter_ = constants[0]["control_behavior"]["sections"]["sections"][0]["filters"][0]
    assert filter_ == {
        "index": 1,
        "name": "signal-F",
        "quality": "normal",
        "comparator": "=",
        "count": 1,
        "type": "virtual",
    }


def test_layout_encoder_adds_uniform_in_game_diagnostic_annotations() -> None:
    control = SignalId("virtual", "signal-C")
    output = SignalId("virtual", "signal-R")
    circuit = PhysicalCircuit(
        "annotations",
        entities=[
            ArithmeticCombinator(
                id=1,
                operation="+",
                left=Operand(constant=2),
                right=Operand(constant=3),
                output_each=False,
                output_signal=output,
            ),
            DeciderCombinator(
                id=2,
                comparator="!=",
                left=Operand(signal=control),
                right=Operand(constant=0),
                output_signal=output,
                description="test gate",
            ),
            ConstantCombinator(
                id=3,
                description="INPUT movement — whole signal vector",
                annotation_only=True,
            ),
        ],
    )
    layout = Layout(
        circuit,
        {1: (0.0, 0.0), 2: (3.0, 0.0), 3: (6.0, 0.0)},
        (LayoutRelay(4, (9.0, 0.0), "route group 7"),),
        (),
        (),
        (),
    )

    blueprint = layout_to_blueprint_json(layout)["blueprint"]
    entities = {entity["entity_number"]: entity for entity in blueprint["entities"]}

    assert entities[1]["player_description"] == "[FCC #1 | arithmetic +]"
    assert entities[2]["player_description"] == "[FCC #2 | decider !=] test gate"
    assert entities[3]["player_description"] == (
        "[FCC #3 | marker] INPUT movement — whole signal vector"
    )
    assert entities[4]["player_description"] == "[FCC #4 | relay] route group 7"


def test_layout_encoder_rejects_unsupported_decider_else_output() -> None:
    control = SignalId("virtual", "signal-C")
    output = SignalId("virtual", "signal-R")
    otherwise = SignalId("virtual", "signal-S")
    circuit = PhysicalCircuit(
        "unsupported_else",
        entities=[
            DeciderCombinator(
                id=1,
                comparator="==",
                left=Operand(signal=control),
                right=Operand(constant=0),
                output_signal=output,
                else_output_signal=otherwise,
            )
        ],
    )
    layout = Layout(circuit, {1: (0.0, 0.0)}, (), (), (), ())

    with pytest.raises(ValueError, match="does not support decider else outputs"):
        layout_to_blueprint_json(layout)
