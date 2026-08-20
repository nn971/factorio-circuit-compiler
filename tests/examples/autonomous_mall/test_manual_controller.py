import pytest

from examples.autonomous_mall.manual_controller import DEFAULT_WORKERS, build_manual_controller
from factorio_circuit import compile_circuit
from factorio_circuit.ir.state import FreezeRegister


def _expected_scalar_inputs() -> set[str]:
    result = {"dispatch"}
    for worker in DEFAULT_WORKERS:
        result.update(
            {
                f"{worker.name}_job_enable",
                f"{worker.name}_working",
                f"{worker.name}_finished",
            }
        )
    return result


def _expected_vector_inputs() -> set[str]:
    result = {"stock"}
    for worker in DEFAULT_WORKERS:
        result.add(f"{worker.name}_job_request")
        if worker.uses_recipe_command:
            result.add(f"{worker.name}_job_recipe")
    return result


def _expected_state_names() -> set[str]:
    result = {"dispatch_seen"}
    for worker in DEFAULT_WORKERS:
        result.update({f"{worker.name}_mode", f"{worker.name}_request"})
        if worker.uses_recipe_command:
            result.add(f"{worker.name}_recipe")
    return result


def _expected_outputs() -> set[str]:
    result = {"batch_ready", "dispatch_armed", "any_accepted", "remaining_snapshot"}
    for worker in DEFAULT_WORKERS:
        result.update(
            {
                f"{worker.name}_requester_demand",
                f"{worker.name}_accepted",
                f"{worker.name}_busy",
                f"{worker.name}_waiting_finished",
                f"{worker.name}_ack_finished",
            }
        )
        if worker.uses_recipe_command:
            result.add(f"{worker.name}_recipe")
    return result


def test_manual_controller_semantic_shape_is_stable() -> None:
    module = build_manual_controller().build()

    assert {item.name for item in module.inputs} == _expected_scalar_inputs()
    assert {item.name for item in module.vector_inputs} == _expected_vector_inputs()
    assert {register.name for register in module.state_registers} == _expected_state_names()
    assert all(isinstance(register, FreezeRegister) for register in module.state_registers)
    assert {name for name in module.output.names if name is not None} == _expected_outputs()


@pytest.mark.slow
@pytest.mark.acceptance
def test_manual_controller_full_compile_acceptance() -> None:
    result = compile_circuit(build_manual_controller())

    assert {port.name for port in result.physical_circuit.inputs} == (
        _expected_scalar_inputs() | _expected_vector_inputs()
    )
    assert {port.name for port in result.physical_circuit.outputs} == _expected_outputs()
    assert result.physical_circuit.combinator_count > 0
    assert result.blueprint_string.startswith("0")
