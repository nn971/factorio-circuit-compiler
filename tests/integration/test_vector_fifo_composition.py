import pytest

from factorio_circuit import SignalId, compile_circuit
from factorio_circuit.frontend import Circuit

DEPTH = 4
COUNT_SIGNAL = SignalId("virtual", "signal-Q")


def _vector_fifo() -> Circuit:
    circuit = Circuit("vector_fifo_test")
    request = circuit.signals("request")
    push = circuit.input("push") != 0
    pop = circuit.input("pop") != 0

    plus_one = circuit.constant_signals({COUNT_SIGNAL: 1})
    minus_one = circuit.constant_signals({COUNT_SIGNAL: -1})

    length_reg = circuit.accumulator("length")
    slots = [circuit.freeze(f"slot{index}") for index in range(DEPTH)]

    old_length = length_reg.value.signal(COUNT_SIGNAL)
    old_slots = [slot.value for slot in slots]

    length_reg.add(plus_one, when=push)
    length_reg.add(minus_one, when=pop)

    tail_index = old_length - pop
    for index, slot in enumerate(slots):
        push_here = push * (tail_index == index)
        injected = request.gate(push_here)
        next_value = old_slots[index + 1].gate(pop) + injected if index + 1 < DEPTH else injected
        slot.set(next_value, when=pop | push_here)

    circuit.output("front", old_slots[0])
    circuit.output("empty", old_length == 0)
    circuit.output("full", old_length == DEPTH)

    circuit.tick(1)
    circuit.output("next_front", slots[0].value)
    circuit.output("next_length", length_reg.value.signal(COUNT_SIGNAL))
    return circuit


@pytest.mark.parametrize("optimize", [False, True])
def test_four_slot_vector_fifo_composes_from_existing_registers(optimize: bool) -> None:
    result = compile_circuit(_vector_fifo(), optimize=optimize)
    timing = {item.register.name: item for item in result.state_timing.registers}

    assert set(timing) == {"length", "slot0", "slot1", "slot2", "slot3"}
    assert timing["slot0"].state_phase > timing["slot1"].state_phase
    assert timing["slot1"].state_phase > timing["slot2"].state_phase
    assert timing["slot2"].state_phase > timing["slot3"].state_phase
    assert all(item.commit_offset == 0 for item in timing.values())

    output_names = {port.name for port in result.physical_circuit.outputs}
    assert output_names == {"front", "empty", "full", "next_front", "next_length"}
