from factorio_circuit import Circuit, SignalId, compile_circuit
from factorio_circuit.ir.output import OutputMaterializationPolicy
from factorio_circuit.simulate.physical import simulate_stream

IRON = SignalId("item", "iron-plate")
COPPER = SignalId("item", "copper-plate")


def test_sum_into_accepts_event_merge_source_and_gate_clock_target_physically() -> None:
    circuit = Circuit("derived_sum_into_physical")
    enabled = circuit.input("enabled")
    left = circuit.signal_event("left", guaranteed_min_separation=4)
    right = circuit.signal_event("right", guaranteed_min_separation=4)
    tick = circuit.event("tick", guaranteed_min_separation=4)

    merged = circuit.event_merge(left, right)
    target = circuit.gate_clock(tick, when=circuit.sample_on(enabled, tick))
    summed = circuit.sum_into(merged, target)
    circuit.output("sum", summed, policy=OutputMaterializationPolicy.VALID)

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
        "sum",
        "sum__valid",
    ]
    assert all(not port.name.startswith(merged.name) for port in compiled.abstract_physical.inputs)
    assert all(not port.name.startswith(target.name) for port in compiled.abstract_physical.inputs)
    assert all(not port.name.startswith(summed.name) for port in compiled.abstract_physical.inputs)

    payload_phase = compiled.abstract_physical.outputs[0].phase
    valid_phase = compiled.abstract_physical.outputs[1].phase
    assert payload_phase == valid_phase
    phase = valid_phase

    rows = []
    for tick_index in range(9):
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
            row["left"] = {IRON: 2}
            row["left__valid"] = 1
        if tick_index == 2:
            row["right"] = {COPPER: 3}
            row["right__valid"] = 1
        if tick_index == 4:
            # The source and target occur logically together. Right-closed SumInto semantics must
            # include this new source contribution in the target snapshot.
            row["left"] = {IRON: 5}
            row["left__valid"] = 1
            row["enabled"] = 1
            row["tick"] = 1
            row["tick__valid"] = 1
        if tick_index == 8:
            # The second interval must start empty after the t=4 snapshot.
            row["right"] = {COPPER: 4}
            row["right__valid"] = 1
            row["enabled"] = 1
            row["tick"] = 1
            row["tick__valid"] = 1
        rows.append(row)

    trace = simulate_stream(compiled.physical_circuit, rows, flush_ticks=phase)

    assert trace[4 + phase] == ({IRON: 7, COPPER: 3}, 1)
    assert trace[8 + phase] == ({COPPER: 4}, 1)
    assert all(
        valid == 0 for index, (_, valid) in enumerate(trace) if index not in {4 + phase, 8 + phase}
    )
