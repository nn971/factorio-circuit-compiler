import pytest

from factorio_circuit import compile_circuit
from factorio_circuit.frontend import Circuit

DEPTH = 4


def _vector_stack() -> Circuit:
    circuit = Circuit("vector_stack_test")
    push_data = circuit.signals("push_data")
    push_requested = circuit.input("push") != 0
    pop_requested = circuit.input("pop") != 0

    slots = [circuit.freeze(f"slot{index}") for index in range(DEPTH)]
    old_slots = [slot.sample() for slot in slots]

    nonempty = old_slots[0].any()
    full = old_slots[-1].any()
    pop = pop_requested * nonempty
    push = push_requested * pop_requested.logical_not() * full.logical_not()
    change = push | pop

    for index, slot in enumerate(slots):
        pushed = push_data.gate(push) if index == 0 else old_slots[index - 1].gate(push)
        next_value = pushed + old_slots[index + 1].gate(pop) if index + 1 < DEPTH else pushed
        slot.set(next_value, when=change)

    circuit.output("top", old_slots[0])
    circuit.output("empty", nonempty.logical_not())
    circuit.output("full", full)
    circuit.output("push_accepted", push)
    circuit.output("pop_accepted", pop)

    circuit.step()
    circuit.output("next_top", slots[0].sample())
    return circuit


@pytest.mark.parametrize("optimize", [False, True])
def test_four_slot_vector_stack_uses_only_primitive_freeze_registers(optimize: bool) -> None:
    result = compile_circuit(_vector_stack(), optimize=optimize)
    timing = {item.register.name: item for item in result.state_timing.registers}

    assert set(timing) == {"slot0", "slot1", "slot2", "slot3"}
    assert len(result.state_timing.domains) == 1
    period = result.state_timing.domains[0].period
    assert period > 1
    assert all(item.period == period for item in timing.values())
    assert all(item.transition_input_phase >= item.earliest_transition_input_phase for item in timing.values())

    assert any(
        getattr(entity, "description", "") == f"clock domain 0: modulo-{period} counter"
        for entity in result.physical_circuit.entities
    )
    output_names = {port.name for port in result.physical_circuit.outputs}
    assert output_names == {
        "top",
        "empty",
        "full",
        "push_accepted",
        "pop_accepted",
        "next_top",
    }
