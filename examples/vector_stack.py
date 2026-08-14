"""Four-slot runtime-open vector stack built only from FreezeReg primitives.

Slot 0 is the top.  Push shifts existing tasks away from the top; pop shifts them toward it.
The compact-prefix invariant makes slot0 empty mean the stack is empty and slot3 nonempty mean it
is full, so no separate length register is needed.  Pop has priority if both external commands are
asserted; callers normally keep them mutually exclusive.
"""

from factorio_circuit import compile_circuit
from factorio_circuit.frontend import Circuit

DEPTH = 4

circuit = Circuit("vector_stack")
push_data = circuit.signals("push_data")
push_requested = circuit.input("push") != 0
pop_requested = circuit.input("pop") != 0

slots = [circuit.freeze(f"slot{index}") for index in range(DEPTH)]
old_slots = [slot.sample() for slot in slots]

nonempty = old_slots[0].any()
full = old_slots[-1].any()
empty = nonempty.logical_not()
not_full = full.logical_not()

# Pop wins if both commands are asserted.  The autonomous-market controller will issue only one
# stack mutation per logical transition, but making the standalone example deterministic is useful.
pop = pop_requested * nonempty
push = push_requested * pop_requested.logical_not() * not_full
change = push | pop

circuit.output("top", old_slots[0])
circuit.output("empty", empty)
circuit.output("full", full)
circuit.output("push_accepted", push)
circuit.output("pop_accepted", pop)

for index, slot in enumerate(slots):
    # Push: [A, B, C, _] -> [X, A, B, C]
    pushed = push_data.gate(push) if index == 0 else old_slots[index - 1].gate(push)

    # Pop: [A, B, C, _] -> [B, C, _, _].  On the last slot, pushed is already empty when pop is
    # active, so no explicit empty-vector source is needed.
    next_value = pushed + old_slots[index + 1].gate(pop) if index + 1 < DEPTH else pushed

    slot.set(next_value, when=change)

circuit.step()
new_slots = [slot.sample() for slot in slots]
circuit.output("next_top", new_slots[0])
circuit.output("next_empty", new_slots[0].any().logical_not())
circuit.output("next_full", new_slots[-1].any())
for index, value in enumerate(new_slots):
    circuit.output(f"slot{index}", value)

result = compile_circuit(circuit)
print(result.blueprint_string)
