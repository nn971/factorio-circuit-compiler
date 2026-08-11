import base64
import json
import zlib

from factorio_circuit import Circuit, SignalId, compile_circuit


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
