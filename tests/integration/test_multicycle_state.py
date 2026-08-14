from factorio_circuit import Circuit, SignalId, compile_circuit
from factorio_circuit.simulate.physical import simulate_stream

COUNT = SignalId("virtual", "signal-Q")


def _self_limited_accumulator() -> Circuit:
    circuit = Circuit("self_limited_accumulator")
    one = circuit.constant_signals({COUNT: 1})
    memory = circuit.accumulator("memory")

    old_count = memory.sample().signal(COUNT)
    memory.add(one, when=old_count < 2)

    circuit.step(1)
    circuit.output("count", memory.sample())
    return circuit


def test_multicycle_accumulator_commits_only_at_logical_boundaries() -> None:
    result = compile_circuit(_self_limited_accumulator(), optimize=False)
    timing = result.state_timing.registers[0]

    # state compare + control normalization + state-writing gate
    assert timing.period == 3
    assert timing.state_phase == 0
    assert result.physical_circuit.outputs[0].phase == 3

    observations = simulate_stream(result.physical_circuit, [{} for _ in range(10)], flush_ticks=3)
    values = [row[0] for row in observations]

    assert values[3].get(COUNT, 0) == 1
    assert values[4].get(COUNT, 0) == 1
    assert values[5].get(COUNT, 0) == 1
    assert values[6].get(COUNT, 0) == 2
    assert values[7].get(COUNT, 0) == 2
    assert values[8].get(COUNT, 0) == 2
    assert values[9].get(COUNT, 0) == 2
