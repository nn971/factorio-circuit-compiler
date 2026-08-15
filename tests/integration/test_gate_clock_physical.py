from factorio_circuit import Circuit, SignalId, compile_circuit
from factorio_circuit.ir.output import OutputMaterializationPolicy
from factorio_circuit.simulate.physical import simulate_stream

IRON = SignalId("item", "iron-plate")


def test_gate_clock_physical_valid_is_filtered_independently_of_parent_payload() -> None:
    circuit = Circuit("gate_clock_physical")
    enabled = circuit.input("enabled")
    parent = circuit.event("parent", guaranteed_min_separation=2)
    gated = circuit.gate_clock(parent, when=circuit.sample_on(enabled, parent))
    circuit.output("gated", gated, policy=OutputMaterializationPolicy.VALID)

    compiled = compile_circuit(circuit)
    assert [port.name for port in compiled.abstract_physical.inputs] == [
        "enabled",
        "parent",
        "parent__valid",
    ]
    assert [port.name for port in compiled.abstract_physical.outputs] == [
        "gated",
        "gated__valid",
    ]
    assert compiled.abstract_physical.outputs[0].phase == 2
    assert compiled.abstract_physical.outputs[1].phase == 2

    trace = simulate_stream(
        compiled.physical_circuit,
        [
            {"enabled": 0, "parent": 7, "parent__valid": 1},
            {"enabled": 1, "parent": 99, "parent__valid": 0},
            # A zero-valued parent occurrence is still present. The gate depends on the explicit
            # sampled Level predicate, not on payload truthiness, so this occurrence must survive.
            {"enabled": 1, "parent": 0, "parent__valid": 1},
            {"enabled": 0, "parent": 5, "parent__valid": 0},
            {"enabled": 1, "parent": 4, "parent__valid": 1},
        ],
        flush_ticks=2,
    )

    assert trace[2] == (0, 0)
    assert trace[4] == (1, 1)
    assert trace[6] == (1, 1)
    assert all(valid == 0 for index, (_, valid) in enumerate(trace) if index not in {4, 6})


def test_chained_gate_clocks_share_derived_valid_without_external_ports() -> None:
    circuit = Circuit("gate_clock_chain_physical")
    parent = circuit.event("parent", guaranteed_min_separation=4)
    first = circuit.gate_clock(parent, when=parent > 0)
    second = circuit.gate_clock(first, when=first != 0)
    circuit.output("second", second, policy=OutputMaterializationPolicy.VALID)

    compiled = compile_circuit(circuit)
    assert [port.name for port in compiled.abstract_physical.inputs] == [
        "parent",
        "parent__valid",
    ]
    assert [port.name for port in compiled.abstract_physical.outputs] == [
        "second",
        "second__valid",
    ]
    assert all(not port.name.startswith(first.name) for port in compiled.abstract_physical.inputs)
    assert all(not port.name.startswith(second.name) for port in compiled.abstract_physical.inputs)

    phase = compiled.abstract_physical.outputs[1].phase
    trace = simulate_stream(
        compiled.physical_circuit,
        [
            {"parent": -3, "parent__valid": 1},
            {"parent": 99, "parent__valid": 0},
            {"parent": 0, "parent__valid": 0},
            {"parent": 0, "parent__valid": 0},
            {"parent": 5, "parent__valid": 1},
        ],
        flush_ticks=phase,
    )

    assert trace[phase] == (0, 0)
    assert trace[4 + phase] == (1, 1)
    assert sum(valid for _, valid in trace) == 1


def test_gate_clock_driven_freeze_delays_level_snapshot_to_derived_valid_phase() -> None:
    circuit = Circuit("gate_clock_state_physical")
    enabled = circuit.input("enabled")
    data = circuit.signals("data")
    parent = circuit.event("parent", guaranteed_min_separation=2)
    gated = circuit.gate_clock(parent, when=circuit.sample_on(enabled, parent))
    sampled_data = circuit.sample_on(data, gated)
    memory = circuit.freeze("memory")
    memory.set(sampled_data, when=1)
    circuit.output("memory", memory.sample())

    compiled = compile_circuit(circuit)
    trace = simulate_stream(
        compiled.physical_circuit,
        [
            {"enabled": 0, "data": {IRON: 10}, "parent": 1, "parent__valid": 1},
            {"enabled": 0, "data": {IRON: 99}, "parent": 0, "parent__valid": 0},
            {"enabled": 1, "data": {IRON: 20}, "parent": 1, "parent__valid": 1},
            {"enabled": 1, "data": {IRON: 88}, "parent": 0, "parent__valid": 0},
            {"enabled": 0, "data": {IRON: 30}, "parent": 1, "parent__valid": 1},
        ],
        flush_ticks=2,
    )

    assert trace[0] == ({},)
    # The accepted t=2 occurrence commits after the one-tick GateClock and one-tick freeze update.
    # The captured payload is data(t=2)=20, not the later Level rows seen while valid is delayed.
    assert trace[4] == ({IRON: 20},)
    assert trace[-1] == ({IRON: 20},)
