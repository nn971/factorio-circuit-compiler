from factorio_circuit import Circuit, compile_circuit
from factorio_circuit.ir.physical import DeciderCombinator
from factorio_circuit.ir.semantic import Compare, Select
from factorio_circuit.simulate.compare import assert_equivalent_random


def _choose() -> Circuit:
    c = Circuit("choose")
    a = c.input("a")
    b = c.input("b")
    value = (a > b).select((a + 1) * 2, b - 3)
    c.output("value", value)
    return c


def _truthy() -> Circuit:
    c = Circuit("truthy")
    a = c.input("a")
    b = c.input("b")
    c.output("value", a.select(b * 4, b))
    return c


def test_select_lowers_to_compare_select_and_decider() -> None:
    result = compile_circuit(_choose())
    assert any(isinstance(op, Compare) for op in result.semantic_ir.operations)
    assert any(isinstance(op, Select) for op in result.semantic_ir.operations)
    assert any(isinstance(entity, DeciderCombinator) for entity in result.physical_circuit.entities)
    assert_equivalent_random(result.semantic_ir, result.physical_circuit, cases=100, seed=23)


def test_integer_truthiness_selects_on_nonzero() -> None:
    result = compile_circuit(_truthy())
    assert_equivalent_random(result.semantic_ir, result.physical_circuit, cases=80, seed=29)
