from factorio_circuit import Circuit
from factorio_circuit.ir.semantic import BinaryOp
from factorio_circuit.optimize.pipeline import optimize_semantic


def _redundant() -> Circuit:
    c = Circuit("redundant")
    a = c.input("a")
    b = c.input("b")
    x = a + b
    y = a + b
    _dead = a * 99
    z = x + 0
    c.output("z", z)
    c.output("y", y)
    return c


def test_simplification_cse_and_dead_code() -> None:
    before = _redundant().build()
    after = optimize_semantic(before)
    binary = [op for op in after.operations if isinstance(op, BinaryOp)]
    assert len(before.operations) == 4
    assert len(binary) == 1
    assert after.output.values[0] is after.output.values[1]
    assert after.output.names == ("z", "y")
