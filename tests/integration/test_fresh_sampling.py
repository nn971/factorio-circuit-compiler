from factorio_circuit import Circuit, compile_circuit
from factorio_circuit.simulate.compare import assert_same_stream


def test_fresh_input_sample_uses_live_source_at_later_logical_tick() -> None:
    c = Circuit("fresh_sample")
    x = c.input("x")
    x0 = x
    c.tick(2)
    x2 = x.sample()
    c.output("sum", x0 + x2)

    result = compile_circuit(c, optimize=False)
    assert result.physical_circuit.outputs[0].phase == 3
    assert_same_stream(
        result.semantic_ir,
        result.physical_circuit,
        [{"x": 10}, {"x": 20}, {"x": 30}, {"x": 40}, {"x": 50}],
    )


def test_fresh_vector_sample_can_feed_state_after_later_freshness() -> None:
    c = Circuit("fresh_vector_state")
    data = c.signals("data")
    clear = c.input("clear")
    memory = c.accumulator("memory")
    c.tick(3)
    memory.add(data.sample())
    memory.clear(when=clear)
    c.output("memory", memory.value)

    result = compile_circuit(c, optimize=False)
    assert result.physical_circuit.connections
    assert any(
        getattr(entity, "description", None) == "phase alignment delay"
        for entity in result.physical_circuit.entities
    )
