from math import hypot

from factorio_circuit import Circuit, SignalId, compile_circuit
from factorio_circuit.devices import AnchorSpec
from factorio_circuit.devices.compiled_anchors import (
    CompiledAnchorBinding,
    compiled_module_as_anchored_blueprint,
)
from factorio_circuit.devices.protocol import DevicePortDirection
from factorio_circuit.ir.physical import WireColor
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality

EXT_IN = SignalId("virtual", "signal-I")
EXT_OUT = SignalId("virtual", "signal-O")


def _compile_smoke():
    circuit = Circuit("compiled_anchor_smoke")
    value = circuit.input("value")
    circuit.output("result", value + 1)
    return compile_circuit(circuit, optimize=False)


def _bindings(result) -> tuple[CompiledAnchorBinding, ...]:
    positions = result.layout.positions
    inputs = {port.name: port for port in result.physical_circuit.inputs}
    outputs = {port.name: port for port in result.physical_circuit.outputs}
    input_position = positions[inputs["value"].marker_entity]
    output_position = positions[outputs["result"].marker_entity]
    return (
        CompiledAnchorBinding(
            "value",
            AnchorSpec(
                "value_in",
                DevicePortDirection.INPUT,
                PayloadShape.SCALAR,
                TemporalModality.LEVEL,
                WireColor.GREEN,
                EXT_IN,
            ),
            (input_position[0] - 7.0, input_position[1]),
        ),
        CompiledAnchorBinding(
            "result",
            AnchorSpec(
                "result_out",
                DevicePortDirection.OUTPUT,
                PayloadShape.SCALAR,
                TemporalModality.LEVEL,
                WireColor.RED,
                EXT_OUT,
            ),
            (output_position[0] + 7.0, output_position[1]),
        ),
    )


def _entity_with_description(blueprint, description: str):
    return next(
        entity
        for entity in blueprint["entities"]
        if entity.get("player_description") == description
    )


def test_compiled_module_ports_normalize_to_typed_colored_anchors() -> None:
    result = _compile_smoke()
    anchored = compiled_module_as_anchored_blueprint(result, _bindings(result))

    assert anchored.anchor("value_in").connector_id == 2
    assert anchored.anchor("result_out").connector_id == 1
    descriptions = {
        str(entity.get("player_description", "")) for entity in anchored.blueprint["entities"]
    }
    assert "ANCHOR ADAPTER value_in" in descriptions
    assert "ANCHOR ADAPTER result_out" in descriptions

    positions = {
        int(entity["entity_number"]): (
            float(entity["position"]["x"]),
            float(entity["position"]["y"]),
        )
        for entity in anchored.blueprint["entities"]
    }
    for left, _lc, right, _rc in anchored.blueprint["wires"]:
        assert (
            hypot(
                positions[int(left)][0] - positions[int(right)][0],
                positions[int(left)][1] - positions[int(right)][1],
            )
            <= 7.5 + 1e-9
        )


def test_compiled_anchor_adapter_moves_around_existing_relay() -> None:
    result = _compile_smoke()
    bindings = _bindings(result)
    baseline = compiled_module_as_anchored_blueprint(result, bindings)
    baseline_adapter = _entity_with_description(baseline.blueprint, "ANCHOR ADAPTER value_in")
    blocked_position = {
        "x": float(baseline_adapter["position"]["x"]),
        "y": float(baseline_adapter["position"]["y"]),
    }

    blueprint = result.blueprint_json["blueprint"]
    entities = blueprint["entities"]
    blocker_id = max(int(entity["entity_number"]) for entity in entities) + 1
    entities.append(
        {
            "entity_number": blocker_id,
            "name": "constant-combinator",
            "position": dict(blocked_position),
            "player_description": "WIRE RELAY — legalization regression blocker",
        }
    )

    legalized = compiled_module_as_anchored_blueprint(result, bindings)
    adapter = _entity_with_description(legalized.blueprint, "ANCHOR ADAPTER value_in")
    adapter_position = (
        float(adapter["position"]["x"]),
        float(adapter["position"]["y"]),
    )
    blocker_position = (blocked_position["x"], blocked_position["y"])

    assert adapter_position != blocker_position
    # Direction 4 arithmetic combinators occupy 1x2 tiles; the blocker is a 1x1 constant.
    assert (
        abs(adapter_position[0] - blocker_position[0]) >= 1.0
        or abs(adapter_position[1] - blocker_position[1]) >= 1.5
    )

    positions = {
        int(entity["entity_number"]): (
            float(entity["position"]["x"]),
            float(entity["position"]["y"]),
        )
        for entity in legalized.blueprint["entities"]
    }
    for left, _lc, right, _rc in legalized.blueprint["wires"]:
        assert (
            hypot(
                positions[int(left)][0] - positions[int(right)][0],
                positions[int(left)][1] - positions[int(right)][1],
            )
            <= 7.5 + 1e-9
        )
