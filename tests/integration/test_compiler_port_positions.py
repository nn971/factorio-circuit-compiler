from __future__ import annotations

import pytest

from factorio_circuit import Circuit, compile_circuit


def _module() -> Circuit:
    circuit = Circuit("pinned_ports")
    left = circuit.input("left")
    right = circuit.input("right")
    circuit.output("sum", left + right)
    return circuit


def test_named_port_positions_pin_markers_before_placement() -> None:
    result = compile_circuit(
        _module(),
        optimize=False,
        port_positions={
            "left": (-10.0, 2.0),
            "right": (-10.0, 6.0),
            "sum": (10.0, 4.0),
        },
    )

    positions = result.layout.positions
    inputs = {port.name: port for port in result.physical_circuit.inputs}
    outputs = {port.name: port for port in result.physical_circuit.outputs}
    assert positions[inputs["left"].marker_entity] == (-10.0, 2.0)
    assert positions[inputs["right"].marker_entity] == (-10.0, 6.0)
    assert positions[outputs["sum"].marker_entity] == (10.0, 4.0)


def test_unknown_named_port_position_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown compiler port position"):
        compile_circuit(
            _module(),
            optimize=False,
            port_positions={"missing": (0.0, 0.0)},
        )
