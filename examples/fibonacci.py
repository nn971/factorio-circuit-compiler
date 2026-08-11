"""Switchable Fibonacci generator using two mutually coupled vector registers."""

from factorio_circuit import Circuit, SignalId, compile_circuit

FIB = SignalId("virtual", "signal-F")

c = Circuit("switchable_fibonacci")
on = c.input("on")
one = c.constant_signals({FIB: 1})

a = c.freeze("fib_a")
b = c.accumulator("fib_b")

old_a = a.value
old_b = b.value

a.set(old_b, when=on)
b.add(old_a, when=on)
b.add(one, when=on)

# Read the post-transition boundary.  For this affine recurrence, B-A is exactly
# 1, 1, 2, 3, 5, ... while on is nonzero, and it holds when on is zero.
c.tick(1)
new_a = a.value
new_b = b.value
c.output("fib", new_b.signal(FIB) - new_a.signal(FIB))

result = compile_circuit(c, optimize=False)
print(result.blueprint_string)
