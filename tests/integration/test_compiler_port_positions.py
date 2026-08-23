from __future__ import annotations

import pytest

from factorio_circuit import Circuit, compile_circuit


def _module() -> Circuit:
    circuit = Circuit("pinned_ports")
    left = circuit.input("left")
    right = circuit.input("right")
    circuit.output("sum", left + right)
    return circuit


def _assert_port_positions(result: object) -> None:
    layout = result.layout
    physical = result.physical_circuit
    positions = layout.positions
    inputs = {port.name: port for port in physical.inputs}
    outputs = {port.name: port for port in physical.outputs}
    assert positions[inputs["left"].marker_entity] == (-10.0, 2.0)
    assert positions[inputs["right"].marker_entity] == (-10.0, 6.0)
    assert positions[outputs["sum"].marker_entity] == (10.0, 4.0)


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

    _assert_port_positions(result)


def test_optimized_port_positions_do_not_constrain_comparison_baseline() -> None:
    result = compile_circuit(
        _module(),
        optimize=True,
        port_positions={
            "left": (-10.0, 2.0),
            "right": (-10.0, 6.0),
            "sum": (10.0, 4.0),
        },
    )

    _assert_port_positions(result)
    assert result.naive_physical.combinator_count >= result.physical_circuit.combinator_count


def test_unknown_named_port_position_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown compiler port position"):
        compile_circuit(
            _module(),
            optimize=False,
            port_positions={"missing": (0.0, 0.0)},
        )
