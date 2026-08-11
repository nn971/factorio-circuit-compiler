from factorio_circuit import Circuit, compile_circuit
from factorio_circuit.simulate.compare import assert_equivalent_random, assert_same_stream


def _unequal_depth() -> Circuit:
    c = Circuit("unequal_depth")
    a = c.input("a")
    b = c.input("b")
    x = a + 1
    y = x * 3
    z = y - b
    c.output("z", z)
    return c


def test_nested_dag_wiring_and_phase_alignment() -> None:
    result = compile_circuit(_unequal_depth())
    assert result.physical_circuit.connections
    assert result.physical_circuit.outputs[0].phase == 3
    delay_entities = [
        entity
        for entity in result.physical_circuit.entities
        if getattr(entity, "description", None) == "phase alignment delay"
    ]
    assert len(delay_entities) == 2
    assert_equivalent_random(result.semantic_ir, result.physical_circuit, cases=80, seed=11)
    assert_same_stream(
        result.semantic_ir,
        result.physical_circuit,
        [
            {"a": 1, "b": 10},
            {"a": 9, "b": -3},
            {"a": 0, "b": 7},
            {"a": -20, "b": 4},
        ],
    )
