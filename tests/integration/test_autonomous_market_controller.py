import pytest

from examples.autonomous_market_controller import build_controller
from factorio_circuit import compile_circuit
from factorio_circuit.ir.state import FreezeRegister


EXPECTED_STATE_NAMES = {"mode", "selected_item", "slot0", "slot1", "slot2", "slot3"}
EXPECTED_INPUT_NAMES = {"root_enabled", "worker_working"}
EXPECTED_VECTOR_INPUT_NAMES = {"stock", "root_target", "reader_ingredients"}
EXPECTED_OUTPUT_NAMES = {
    "reader_item",
    "worker_item",
    "mode",
    "top_target",
    "blocked_on_full_stack",
}


def test_market_controller_semantic_shape_is_stable() -> None:
    """Keep a cheap routine regression for the application-scale controller model itself."""

    module = build_controller().build()

    assert {register.name for register in module.state_registers} == EXPECTED_STATE_NAMES
    assert all(isinstance(register, FreezeRegister) for register in module.state_registers)
    assert {item.name for item in module.inputs} == EXPECTED_INPUT_NAMES
    assert {item.name for item in module.vector_inputs} == EXPECTED_VECTOR_INPUT_NAMES
    assert {name for name in module.output.names if name is not None} == EXPECTED_OUTPUT_NAMES


@pytest.mark.slow
@pytest.mark.acceptance
@pytest.mark.parametrize("optimize", [False, True])
def test_market_controller_full_compile_acceptance(optimize: bool) -> None:
    """Exercise full timing/lowering/synthesis only in the opt-in acceptance suite."""

    result = compile_circuit(build_controller(), optimize=optimize)
    timing = {item.register.name: item for item in result.state_timing.registers}

    assert set(timing) == EXPECTED_STATE_NAMES
    assert len(result.state_timing.domains) == 1

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
    assert output_names == EXPECTED_OUTPUT_NAMES
