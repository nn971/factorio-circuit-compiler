import pytest

from examples.autonomous_mall.manual_controller import (
    build_assembler_worker,
    build_recycler_worker,
    build_reservation_cell,
    build_stock_snapshot,
)
from factorio_circuit import compile_circuit
from factorio_circuit.ir.state import FreezeRegister


def _names(items) -> set[str]:
    return {item.name for item in items}


def _output_names(module) -> set[str]:
    return {name for name in module.output.names if name is not None}


def test_stock_snapshot_semantic_shape() -> None:
    module = build_stock_snapshot().build()

    assert _names(module.inputs) == {"dispatch"}
    assert _names(module.vector_inputs) == {"stock"}
    assert _names(module.state_registers) == {"snapshot"}
    assert all(isinstance(register, FreezeRegister) for register in module.state_registers)
    assert _output_names(module) == {"snapshot", "frozen"}


def test_reservation_cell_semantic_shape() -> None:
    module = build_reservation_cell().build()

    assert _names(module.inputs) == {"active", "job_enable"}
    assert _names(module.vector_inputs) == {"available", "job_request"}
    assert not module.state_registers
    assert _output_names(module) == {"accepted", "remaining"}


@pytest.mark.parametrize(
    ("builder", "vector_inputs", "state_names", "outputs"),
    [
        (
            build_assembler_worker,
            {"job_request", "job_recipe"},
            {"mode", "seen", "held_request", "held_recipe"},
            {
                "requester_demand",
                "input_enable",
                "recipe",
                "busy",
                "waiting_finished",
                "ack_finished",
                "armed",
            },
        ),
        (
            build_recycler_worker,
            {"job_request"},
            {"mode", "seen", "held_request"},
            {
                "requester_demand",
                "input_enable",
                "busy",
                "waiting_finished",
                "ack_finished",
                "armed",
            },
        ),
    ],
)
def test_worker_semantic_shape(builder, vector_inputs, state_names, outputs) -> None:
    module = builder().build()

    assert _names(module.inputs) == {"accepted", "launch", "working", "finished"}
    assert _names(module.vector_inputs) == vector_inputs
    assert _names(module.state_registers) == state_names
    assert all(isinstance(register, FreezeRegister) for register in module.state_registers)
    assert _output_names(module) == outputs


@pytest.mark.slow
@pytest.mark.acceptance
@pytest.mark.parametrize(
    "builder",
    [
        build_stock_snapshot,
        build_reservation_cell,
        build_assembler_worker,
        build_recycler_worker,
    ],
)
def test_manual_cell_full_compile_acceptance(builder) -> None:
    result = compile_circuit(builder())

    assert result.physical_circuit.combinator_count > 0
    assert result.blueprint_string.startswith("0")
