"""Filter an Event clock explicitly, then sample Level data on the derived subclock."""

from factorio_circuit import Circuit

circuit = Circuit("gate_clock")
tick = circuit.event("tick", guaranteed_min_separation=2)
enabled = circuit.input("enabled")
data = circuit.signals("data")

# Level data must cross onto the parent Event clock explicitly before it can define a subclock.
enabled_on_tick = circuit.sample_on(enabled, tick)
active = circuit.gate_clock(tick, when=enabled_on_tick)

# ``active`` is a unit-valued derived Event.  It is a subclock of ``tick`` and therefore inherits
# tick's guaranteed minimum separation.  Data sampled on it updates state only on surviving ticks.
memory = circuit.freeze("memory")
memory.set(circuit.sample_on(data, active), when=1)

module = circuit.build()
assert active.clock.guaranteed_min_separation == tick.clock.guaranteed_min_separation
assert module.event_inputs == (tick.ir, active.ir)
