from factorio_circuit import Circuit, compile_circuit
from factorio_circuit.simulate.compare import assert_same_values


def test_naive_physical_matches_semantic() -> None:
    c = Circuit("arithmetic")
    a = c.input("a")
    b = c.input("b")
    c.output("x", a * 3)
    c.output("y", b - 4)

    result = compile_circuit(c, optimize=False)
    assert_same_values(
        result.semantic_ir,
        result.physical_circuit,
        [{"a": 7, "b": -8}, {"a": 2**30, "b": 3}],
    )
