"""Hold a vector Event across clocks using explicit bridge state."""

from factorio_circuit import Circuit

circuit = Circuit("hold_into")
updates = circuit.signal_event("updates", guaranteed_min_separation=2)
refresh = circuit.event("refresh", guaranteed_min_separation=5)

# Unlike SampleOn/GateClock/EventMerge, HoldInto is history-preserving. The frontend elaborates this
# into one hidden FreezeRegister on ``updates`` plus a SampleOn of that register on ``refresh``.
latest_update = circuit.hold_into(updates, refresh)

output = circuit.freeze("output")
output.set(latest_update, when=1)

module = circuit.build()
assert len(module.state_registers) == 2  # hidden hold state + explicit output state

# If updates and refresh occur at the same timestamp, refresh sees the value held before that atomic
# reaction. The simultaneous update becomes visible to the next refresh occurrence.
