"""Merge additive Event streams explicitly before driving shared Event state."""

from factorio_circuit import Circuit

circuit = Circuit("event_merge")
left = circuit.signal_event("left", guaranteed_min_separation=4)
right = circuit.signal_event("right", guaranteed_min_separation=4)

# The merged stream fires whenever either parent fires. Simultaneous parent payloads add together.
# Independent parents can interleave more closely than either source, so the conservative contract
# on this union clock is one tick.
updates = circuit.event_merge(left, right)

memory = circuit.freeze("memory")
memory.set(updates * 1, when=1)

module = circuit.build()
assert updates.clock.guaranteed_min_separation == 1
assert module.event_inputs[-1] is updates.ir
