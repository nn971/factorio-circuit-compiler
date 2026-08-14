"""Four-slot FIFO composed only from existing vector/register primitives.

The queue stores runtime-open one-lane request vectors.  It deliberately assumes valid commands:
``push`` must be false when full and ``pop`` must be false when empty.  That keeps the example free
of queue-specific compiler support and avoids feeding the length register's own predicates back
into its transition.
"""

from factorio_circuit import SignalId, compile_circuit
from factorio_circuit.frontend import Circuit

DEPTH = 4
COUNT_SIGNAL = SignalId("virtual", "signal-Q")

circuit = Circuit("vector_fifo")
request = circuit.signals("request")
push_input = circuit.input("push")
pop_input = circuit.input("pop")

push = push_input != 0
pop = pop_input != 0

plus_one = circuit.constant_signals({COUNT_SIGNAL: 1})
minus_one = circuit.constant_signals({COUNT_SIGNAL: -1})

length_reg = circuit.accumulator("length")
slots = [circuit.freeze(f"slot{index}") for index in range(DEPTH)]

# Snapshot the queue state before this invocation's commands are applied.
old_length_vector = length_reg.value
old_length = old_length_vector.signal(COUNT_SIGNAL)
old_slots = [slot.value for slot in slots]

circuit.output("front", old_slots[0])
circuit.output("length", old_length)
circuit.output("empty", old_length == 0)
circuit.output("full", old_length == DEPTH)

# The length accumulator is the only bookkeeping state.  Because push/pop are assumed valid,
# its updates depend only on external commands rather than on its own full/empty predicates.
length_reg.add(plus_one, when=push)
length_reg.add(minus_one, when=pop)

# With the compact invariant, the insertion position after an optional pop is simply
#     old_length - pop.
# Every pop shifts the cells toward the head.  A simultaneous push is injected into the vacated
# tail position.  Each FreezeReg still receives exactly one ordinary .set(...) transition.
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

# Expose the post-transition state as well, which makes the blueprint convenient to probe in game.
circuit.tick(1)
new_length = length_reg.value.signal(COUNT_SIGNAL)
new_slots = [slot.value for slot in slots]

circuit.output("next_front", new_slots[0])
circuit.output("next_length", new_length)
circuit.output("next_empty", new_length == 0)
circuit.output("next_full", new_length == DEPTH)
for index, value in enumerate(new_slots):
    circuit.output(f"slot{index}", value)

result = compile_circuit(circuit)
print(result.blueprint_string)
