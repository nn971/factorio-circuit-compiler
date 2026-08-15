from factorio_circuit import Circuit, SignalId, compile_circuit
from factorio_circuit.ir.output import OutputMaterializationPolicy
from factorio_circuit.simulate.physical import simulate_stream

IRON = SignalId("item", "iron-plate")
COPPER = SignalId("item", "copper-plate")


def test_hold_into_accepts_event_merge_source_and_gate_clock_target_physically() -> None:
    circuit = Circuit("derived_hold_into_physical")
    enabled = circuit.input("enabled")
    left = circuit.signal_event("left", guaranteed_min_separation=4)
    right = circuit.signal_event("right", guaranteed_min_separation=4)
    tick = circuit.event("tick", guaranteed_min_separation=2)

    merged = circuit.event_merge(left, right)
    target = circuit.gate_clock(tick, when=circuit.sample_on(enabled, tick))
    held = circuit.hold_into(merged, target)
    circuit.output("held", held, policy=OutputMaterializationPolicy.VALID)

    compiled = compile_circuit(circuit)
    assert [port.name for port in compiled.abstract_physical.inputs] == [
        "enabled",
        "left",
        "left__valid",
        "right",
        "right__valid",
        "tick",
        "tick__valid",
    ]
    assert [port.name for port in compiled.abstract_physical.outputs] == [
        "held",
        "held__valid",
    ]

    phase = compiled.abstract_physical.outputs[1].phase
    assert compiled.abstract_physical.outputs[0].phase == phase

    rows = []
    for tick_index in range(7):
        row: dict[str, object] = {
            "enabled": 0,
            "left": {},
            "left__valid": 0,
            "right": {},
            "right__valid": 0,
            "tick": 0,
            "tick__valid": 0,
        }
        if tick_index == 0:
            row["left"] = {IRON: 10}
            row["left__valid"] = 1
        if tick_index in {2, 4, 6}:
            row["enabled"] = 1
            row["tick"] = 1
            row["tick__valid"] = 1
        if tick_index == 4:
            # This source occurrence is simultaneous with the target occurrence. HoldInto samples
            # the strictly prior source value, so copper must not be visible at this target.
            row["right"] = {COPPER: 40}
            row["right__valid"] = 1
        rows.append(row)

    trace = simulate_stream(compiled.physical_circuit, rows, flush_ticks=phase)

    assert trace[2 + phase] == ({IRON: 10}, 1)
    assert trace[4 + phase] == ({IRON: 10}, 1)
    assert trace[6 + phase] == ({COPPER: 40}, 1)
    assert all(
        valid == 0
        for index, (_, valid) in enumerate(trace)
        if index not in {2 + phase, 4 + phase, 6 + phase}
    )
