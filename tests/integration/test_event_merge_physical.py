from factorio_circuit import Circuit, SignalId, compile_circuit
from factorio_circuit.ir.output import OutputMaterializationPolicy
from factorio_circuit.simulate.physical import simulate_stream

IRON = SignalId("item", "iron-plate")
COPPER = SignalId("item", "copper-plate")


def test_scalar_event_merge_adds_simultaneous_payloads_and_normalizes_valid() -> None:
    circuit = Circuit("scalar_event_merge_physical")
    left = circuit.event("left", guaranteed_min_separation=2)
    right = circuit.event("right", guaranteed_min_separation=2)
    merged = circuit.event_merge(left, right)
    circuit.output("merged", merged, policy=OutputMaterializationPolicy.VALID)

    compiled = compile_circuit(circuit)
    assert [port.name for port in compiled.abstract_physical.inputs] == [
        "left",
        "left__valid",
        "right",
        "right__valid",
    ]
    assert [port.name for port in compiled.abstract_physical.outputs] == [
        "merged",
        "merged__valid",
    ]
    assert compiled.abstract_physical.outputs[0].phase == 3
    assert compiled.abstract_physical.outputs[1].phase == 3

    trace = simulate_stream(
        compiled.physical_circuit,
        [
            {"left": 4, "left__valid": 1, "right": 99, "right__valid": 0},
            {"left": 77, "left__valid": 0, "right": -55, "right__valid": 0},
            {"left": 8, "left__valid": 1, "right": -8, "right__valid": 1},
            {"left": 23, "left__valid": 0, "right": 42, "right__valid": 0},
            {"left": 0, "left__valid": 0, "right": 3, "right__valid": 1},
        ],
        flush_ticks=3,
    )

    assert trace[3] == (4, 1)
    # Simultaneous contributions cancel to payload zero, but presence is independent of payload.
    assert trace[5] == (0, 1)
    assert trace[7] == (3, 1)
    assert all(valid == 0 for index, (_, valid) in enumerate(trace) if index not in {3, 5, 7})


def test_vector_event_merge_adds_packed_payloads_and_keeps_empty_occurrence_present() -> None:
    circuit = Circuit("vector_event_merge_physical")
    left = circuit.signal_event("left", guaranteed_min_separation=2)
    right = circuit.signal_event("right", guaranteed_min_separation=2)
    merged = circuit.event_merge(left, right)
    circuit.output("merged", merged, policy=OutputMaterializationPolicy.VALID)

    compiled = compile_circuit(circuit)
    assert [port.name for port in compiled.abstract_physical.inputs] == [
        "left",
        "left__valid",
        "right",
        "right__valid",
    ]
    assert [port.name for port in compiled.abstract_physical.outputs] == [
        "merged",
        "merged__valid",
    ]

    phase = compiled.abstract_physical.outputs[1].phase
    assert compiled.abstract_physical.outputs[0].phase == phase
    trace = simulate_stream(
        compiled.physical_circuit,
        [
            {
                "left": {IRON: 2},
                "left__valid": 1,
                "right": {COPPER: 99},
                "right__valid": 0,
            },
            {"left": {}, "left__valid": 0, "right": {}, "right__valid": 0},
            {
                "left": {IRON: 8, COPPER: 1},
                "left__valid": 1,
                "right": {IRON: -8, COPPER: -1},
                "right__valid": 1,
            },
            {"left": {}, "left__valid": 0, "right": {}, "right__valid": 0},
            {
                "left": {IRON: 3},
                "left__valid": 1,
                "right": {COPPER: 4},
                "right__valid": 1,
            },
        ],
        flush_ticks=phase,
    )

    assert trace[phase] == ({IRON: 2}, 1)
    assert trace[2 + phase] == ({}, 1)
    assert trace[4 + phase] == ({IRON: 3, COPPER: 4}, 1)
    assert sum(valid for _, valid in trace) == 3


def test_event_merge_can_feed_gate_clock_physically_without_new_external_ports() -> None:
    circuit = Circuit("merge_gate_physical")
    left = circuit.event("left", guaranteed_min_separation=3)
    right = circuit.event("right", guaranteed_min_separation=3)
    merged = circuit.event_merge(left, right)
    positive = circuit.gate_clock(merged, when=merged > 0)
    circuit.output("positive", positive, policy=OutputMaterializationPolicy.VALID)

    compiled = compile_circuit(circuit)
    assert [port.name for port in compiled.abstract_physical.inputs] == [
        "left",
        "left__valid",
        "right",
        "right__valid",
    ]
    assert all(not port.name.startswith(merged.name) for port in compiled.abstract_physical.inputs)
    assert all(
        not port.name.startswith(positive.name) for port in compiled.abstract_physical.inputs
    )

    phase = compiled.abstract_physical.outputs[1].phase
    trace = simulate_stream(
        compiled.physical_circuit,
        [
            {"left": 2, "left__valid": 1, "right": 0, "right__valid": 0},
            {"left": 0, "left__valid": 0, "right": 0, "right__valid": 0},
            {"left": 0, "left__valid": 0, "right": 3, "right__valid": 1},
            {"left": 0, "left__valid": 0, "right": 0, "right__valid": 0},
            {"left": -5, "left__valid": 1, "right": 1, "right__valid": 1},
        ],
        flush_ticks=phase,
    )

    assert trace[phase] == (1, 1)
    assert trace[2 + phase] == (1, 1)
    # The simultaneous merge at t=4 is -4, so the positive subclock suppresses it.
    assert trace[4 + phase] == (0, 0)
    assert sum(valid for _, valid in trace) == 2
