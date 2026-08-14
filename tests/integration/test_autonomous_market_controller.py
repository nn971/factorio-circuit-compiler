import pytest

from examples.autonomous_market_controller import build_controller
from factorio_circuit import compile_circuit
from factorio_circuit.ir.state import FreezeRegister


@pytest.mark.parametrize("optimize", [False, True])
def test_market_controller_is_composed_only_from_primitive_freeze_registers(optimize: bool) -> None:
    result = compile_circuit(build_controller(), optimize=optimize)
    timing = {item.register.name: item for item in result.state_timing.registers}

    assert set(timing) == {"mode", "selected_item", "slot0", "slot1", "slot2", "slot3"}
    assert all(
        isinstance(register, FreezeRegister) for register in result.semantic_ir.state_registers
    )
    assert len(result.state_timing.domains) == 1

    input_names = {item.name for item in result.semantic_ir.inputs}
    assert input_names == {"root_enabled", "worker_working"}
    vector_input_names = {item.name for item in result.semantic_ir.vector_inputs}
    assert vector_input_names == {"stock", "root_target", "reader_ingredients"}

    period = result.state_timing.domains[0].period
    assert period > 1
    assert all(item.period == period for item in timing.values())
    assert all(
        item.transition_input_phase >= item.earliest_transition_input_phase
        for item in timing.values()
    )

    assert any(
        getattr(entity, "description", "") == f"clock domain 0: modulo-{period} counter"
        for entity in result.physical_circuit.entities
    )

    output_names = {port.name for port in result.physical_circuit.outputs}
    assert output_names == {
        "reader_item",
        "worker_item",
        "mode",
        "top_target",
        "blocked_on_full_stack",
    }
