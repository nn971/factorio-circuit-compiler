from math import hypot

from factorio_circuit import Circuit, ModuleInterface, SignalId, compile_module
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


def test_compiled_module_ports_normalize_to_typed_colored_anchors() -> None:
    circuit = Circuit("compiled_anchor_smoke")
    value = circuit.input("value")
    circuit.output("result", value + 1)
    result = compile_module(
        circuit,
        ModuleInterface(inputs={"value": (8.0, 4.0)}, outputs={"result": (8.0, 10.0)}),
    )
    anchored = compiled_module_as_anchored_blueprint(
        result,
        (
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
                (0.0, 4.0),
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
                (16.0, 10.0),
            ),
        ),
    )

    assert anchored.anchor("value_in").connector_id == 2
    assert anchored.anchor("result_out").connector_id == 1
    descriptions = {
        str(entity.get("player_description", ""))
        for entity in anchored.blueprint["entities"]
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
        assert hypot(
            positions[int(left)][0] - positions[int(right)][0],
            positions[int(left)][1] - positions[int(right)][1],
        ) <= 7.5 + 1e-9
