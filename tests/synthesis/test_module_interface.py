import base64
import json
import zlib

import pytest

from factorio_circuit import Circuit
from factorio_circuit.synthesis.interface import ModuleInterface, compile_module


def _decode_blueprint_string(value: str) -> dict[str, object]:
    assert value.startswith("0")
    return json.loads(zlib.decompress(base64.b64decode(value[1:])).decode())


def test_named_module_interface_anchors_public_markers_and_snaps_blueprint() -> None:
    circuit = Circuit("module_interface")
    source = circuit.input("source")
    circuit.output("result", source + 1)

    interface = ModuleInterface(
        inputs={"source": (0.0, 3.0)},
        outputs={"result": (16.0, 3.0)},
        grid_size=(16, 8),
        grid_offset=(2, 1),
    )
    result = compile_module(circuit, interface)

    input_port = next(port for port in result.physical_circuit.inputs if port.name == "source")
    output_port = next(port for port in result.physical_circuit.outputs if port.name == "result")
    assert result.layout.positions[input_port.marker_entity] == (0.0, 3.0)
    assert result.layout.positions[output_port.marker_entity] == (16.0, 3.0)

    blueprint = result.blueprint_json["blueprint"]
    assert blueprint["snap-to-grid"] == {"x": 16, "y": 8}
    assert blueprint["absolute-snapping"] is True
    assert blueprint["position-relative-to-grid"] == {"x": 2, "y": 1}
    assert _decode_blueprint_string(result.blueprint_string) == result.blueprint_json


def test_module_interface_rejects_unknown_port_name() -> None:
    circuit = Circuit("bad_module_interface")
    source = circuit.input("source")
    circuit.output("result", source)

    with pytest.raises(ValueError, match="unknown inputs"):
        compile_module(
            circuit,
            ModuleInterface(inputs={"missing": (0.0, 0.0)}),
        )


def test_module_interface_rejects_overlapping_port_anchors() -> None:
    interface = ModuleInterface(
        inputs={"left": (0.0, 0.0)},
        outputs={"right": (0.0, 0.0)},
    )

    with pytest.raises(ValueError, match="distinct positions"):
        interface.validate()
