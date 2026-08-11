from factorio_circuit import Circuit, compile_circuit
from factorio_circuit.simulate.compare import assert_equivalent_random


def _dependent() -> Circuit:
    c = Circuit("dependent")
    a = c.input("a")
    x = a + 1
    y = x * 2
    c.output("y", y)
    return c


def test_dependent_physical_graph_is_now_supported() -> None:
    result = compile_circuit(_dependent())
    assert result.physical_circuit.combinator_count >= 2
    assert result.physical_circuit.connections
    assert_equivalent_random(result.semantic_ir, result.physical_circuit, cases=32, seed=3)
