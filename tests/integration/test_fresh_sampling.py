from factorio_circuit import Circuit, compile_circuit
from factorio_circuit.simulate.compare import assert_same_stream


def test_fresh_input_sample_uses_live_source_at_later_logical_step() -> None:
    c = Circuit("fresh_sample")
    x = c.input("x")
    x0 = x
    c.step(2)
    x2 = x.sample()
    c.output("sum", x0 + x2)

    result = compile_circuit(c, optimize=False)
    assert result.physical_circuit.outputs[0].phase == 3
    assert_same_stream(
        result.semantic_ir,
        result.physical_circuit,
        [{"x": 10}, {"x": 20}, {"x": 30}, {"x": 40}, {"x": 50}],
    )


def test_fresh_vector_sample_can_feed_state_after_later_logical_step() -> None:
    c = Circuit("fresh_vector_state")
    data = c.signals("data")
    clear = c.input("clear")
    memory = c.accumulator("memory")
    c.step(3)
    memory.add(data.sample())
    memory.clear(when=clear)
    c.output("memory", memory.sample())

    result = compile_circuit(c, optimize=False)
    timing = result.state_timing.registers[0]

    assert timing.period == 1
    assert timing.state_phase == 3
    assert timing.earliest_transition_input_phase == 3
    assert timing.transition_input_phase == 3
    assert result.physical_circuit.connections
    # The source sample itself already lives at physical phase 3, so no explicit vector-delay
    # combinator is required merely to align it with this transition.
    assert not any(
        getattr(entity, "description", None) == "vector phase alignment delay"
        for entity in result.physical_circuit.entities
    )
