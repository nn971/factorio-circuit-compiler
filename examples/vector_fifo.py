"""Four-slot FIFO composed only from existing vector/register primitives.

The queue stores runtime-open one-lane request vectors.  Full/empty protection is derived from the
queue's own length register, so this example intentionally exercises a multicycle state recurrence.
No queue-specific compiler primitive is involved.
"""

from factorio_circuit import SignalId, compile_circuit
from factorio_circuit.frontend import Circuit

DEPTH = 4
COUNT_SIGNAL = SignalId("virtual", "signal-Q")

circuit = Circuit("vector_fifo")
request = circuit.signals("request")
push_input = circuit.input("push")
pop_input = circuit.input("pop")

push_requested = push_input != 0
pop_requested = pop_input != 0

plus_one = circuit.constant_signals({COUNT_SIGNAL: 1})
minus_one = circuit.constant_signals({COUNT_SIGNAL: -1})

length_reg = circuit.accumulator("length")
slots = [circuit.freeze(f"slot{index}") for index in range(DEPTH)]

# Snapshot one logical queue state.
old_length_vector = length_reg.sample()
old_length = old_length_vector.signal(COUNT_SIGNAL)
old_slots = [slot.sample() for slot in slots]

empty = old_length == 0
full = old_length == DEPTH
pop = pop_requested * (old_length > 0)
# A pop requested from a full queue frees the tail position in this same logical transition, so a
# simultaneous push is accepted.  On an empty queue, pop is rejected while push remains accepted.
push = push_requested * ((old_length < DEPTH) | pop_requested)

circuit.output("front", old_slots[0])
circuit.output("length", old_length)
circuit.output("empty", empty)
circuit.output("full", full)
circuit.output("push_accepted", push)
circuit.output("pop_accepted", pop)

length_reg.add(plus_one, when=push)
length_reg.add(minus_one, when=pop)

# Compactness is maintained after every logical transition.  After an optional accepted pop, the
# insertion position is old_length-pop.  Every pop shifts toward the head; a simultaneous push is
# injected into the vacated tail.
tail_index = old_length - pop
for index, slot in enumerate(slots):
    push_here = push * (tail_index == index)
    injected = request.gate(push_here)

    if index + 1 < DEPTH:
        shifted = old_slots[index + 1].gate(pop)
        next_value = shifted + injected
    else:
        next_value = injected

    slot.set(next_value, when=pop | push_here)

circuit.step(1)
new_length = length_reg.sample().signal(COUNT_SIGNAL)
new_slots = [slot.sample() for slot in slots]

circuit.output("next_front", new_slots[0])
circuit.output("next_length", new_length)
circuit.output("next_empty", new_length == 0)
circuit.output("next_full", new_length == DEPTH)
for index, value in enumerate(new_slots):
    circuit.output(f"slot{index}", value)

result = compile_circuit(circuit)
print(result.blueprint_string)
