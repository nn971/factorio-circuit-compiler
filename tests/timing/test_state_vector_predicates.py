import pytest

from factorio_circuit import compile_circuit
from factorio_circuit.analysis.state_timing import StateTimingError
from factorio_circuit.frontend import Circuit


@pytest.mark.parametrize("optimize", [False, True])
@pytest.mark.parametrize("register_kind", ["freeze", "accumulator"])
def test_state_vector_any_can_control_another_register(
    register_kind: str, optimize: bool
) -> None:
    circuit = Circuit(f"state_vector_any_{register_kind}")
    data = circuit.signals("data")
    load = circuit.input("load")

    if register_kind == "freeze":
        source = circuit.freeze("source")
    else:
        source = circuit.accumulator("source")
    sink = circuit.freeze("sink")

    old_source = source.value
    if register_kind == "freeze":
        source.set(data, when=load)
    else:
        source.add(data, when=load)
    sink.set(data, when=old_source.any())

    circuit.tick(1)
    circuit.output("sink", sink.value)

    result = compile_circuit(circuit, optimize=optimize)
    source_timing, sink_timing = result.state_timing.registers

    assert source_timing.register.name == "source"
    assert sink_timing.register.name == "sink"
    assert sink_timing.earliest_transition_input_phase == source_timing.state_phase + 2
    assert sink_timing.transition_input_phase == source_timing.state_phase + 2


@pytest.mark.parametrize("optimize", [False, True])
def test_state_vector_predicate_can_gate_vector_state_input(optimize: bool) -> None:
    circuit = Circuit("state_vector_gate")
    data = circuit.signals("data")
    load = circuit.input("load")

    source = circuit.freeze("source")
    sink = circuit.freeze("sink")

    old_source = source.value
    source.set(data, when=load)
    sink.set(data.gate(old_source.any()), when=1)

    circuit.tick(1)
    circuit.output("sink", sink.value)

    result = compile_circuit(circuit, optimize=optimize)
    source_timing, sink_timing = result.state_timing.registers

    assert sink_timing.earliest_transition_input_phase == source_timing.state_phase + 2
    assert sink_timing.transition_input_phase == source_timing.state_phase + 2


def test_state_vector_predicate_self_feedback_keeps_positive_cycle_guard() -> None:
    circuit = Circuit("state_vector_predicate_cycle")
    data = circuit.signals("data")
    memory = circuit.freeze("memory")

    old_memory = memory.value
    memory.set(data, when=old_memory.any())
    circuit.tick(1)
    circuit.output("memory", memory.value)

    with pytest.raises(StateTimingError, match="positive physical latency around a cycle"):
        compile_circuit(circuit, optimize=False)
