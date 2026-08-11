from factorio_circuit import Circuit, compile_circuit
from factorio_circuit.simulate.compare import assert_same_values


def _three_multiplies() -> Circuit:
    c = Circuit("three_multiplies")
    a = c.input("a")
    b = c.input("b")
    d = c.input("c")
    c.output("x", a * 2)
    c.output("y", b * 2)
    c.output("z", d * 2)
    return c


def test_end_to_end_zero_safe_packing() -> None:
    result = compile_circuit(_three_multiplies())
    assert result.naive_physical.combinator_count == 3
    assert result.physical_circuit.combinator_count == 1
    assert result.combinators_saved == 2

    assert_same_values(
        result.semantic_ir,
        result.physical_circuit,
        [
            {"a": 1, "b": 2, "c": 3},
            {"a": -5, "b": 0, "c": 2**31 - 1},
        ],
    )

    assert "blueprint" in result.blueprint_json
    assert result.blueprint_string.startswith("0")
