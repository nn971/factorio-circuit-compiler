import pytest

from factorio_circuit import compile_circuit
from factorio_circuit.frontend import Circuit


@pytest.mark.parametrize("optimize", [False, True])
@pytest.mark.parametrize("register_kind", ["freeze", "accumulator"])
def test_state_vector_any_can_control_another_register(register_kind: str, optimize: bool) -> None:
    circuit = Circuit(f"state_vector_any_{register_kind}")
    data = circuit.signals("data")
    load = circuit.input("load")

    if register_kind == "freeze":
        source = circuit.freeze("source")
    else:
        source = circuit.accumulator("source")
    sink = circuit.freeze("sink")

    old_source = source.sample()
    if register_kind == "freeze":
        source.set(data, when=load)
    else:
        source.add(data, when=load)
    sink.set(data, when=old_source.any())

    circuit.step(1)
    circuit.output("sink", sink.sample())

    result = compile_circuit(circuit, optimize=optimize)
    source_timing, sink_timing = result.state_timing.registers

    assert source_timing.register.name == "source"
    assert sink_timing.register.name == "sink"
    assert source_timing.clock_domain == sink_timing.clock_domain
    assert source_timing.period == sink_timing.period
    assert sink_timing.earliest_transition_input_phase == source_timing.state_phase + 2


@pytest.mark.parametrize("optimize", [False, True])
def test_state_vector_predicate_can_gate_vector_state_input(optimize: bool) -> None:
    circuit = Circuit("state_vector_gate")
    data = circuit.signals("data")
    load = circuit.input("load")

    source = circuit.freeze("source")
    sink = circuit.freeze("sink")

    old_source = source.sample()
    source.set(data, when=load)
    sink.set(data.gate(old_source.any()), when=1)

    circuit.step(1)
    circuit.output("sink", sink.sample())

    result = compile_circuit(circuit, optimize=optimize)
    source_timing, sink_timing = result.state_timing.registers

    assert source_timing.clock_domain == sink_timing.clock_domain
    assert sink_timing.earliest_transition_input_phase >= source_timing.state_phase + 2


@pytest.mark.parametrize("optimize", [False, True])
def test_state_vector_predicate_self_feedback_infers_three_tick_period(optimize: bool) -> None:
    circuit = Circuit("state_vector_predicate_cycle")
    data = circuit.signals("data")
    memory = circuit.freeze("memory")

    old_memory = memory.sample()
    memory.set(data, when=old_memory.any())
    circuit.step(1)
    circuit.output("memory", memory.sample())

    result = compile_circuit(circuit, optimize=optimize)
    timing = result.state_timing.registers[0]

    assert len(result.state_timing.domains) == 1
    assert timing.period == 3
    assert result.state_timing.domains[0].period == 3
    assert timing.transition_input_phase == 2
    assert timing.reads[-1].physical_phase == 3
    assert any(
        getattr(entity, "description", "") == "clock domain 0: modulo-3 counter"
        for entity in result.physical_circuit.entities
    )
