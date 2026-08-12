import pytest

from factorio_circuit import Circuit, CircuitBuildError
from factorio_circuit.ir.semantic import BinaryOp, InputSample, VectorInputSample
from factorio_circuit.ir.state import (
    AccumulatorAdd,
    AccumulatorClear,
    FreezeSet,
    VectorRegisterRead,
)


def test_symbolic_straight_line_builds_ir() -> None:
    c = Circuit("sample")
    a = c.input("a")
    b = c.input("b")
    x = a + 1
    y = b * 2
    c.output("x", x)
    c.output("y", y)

    module = c.build()
    assert module.name == "sample"
    assert [item.name for item in module.inputs] == ["a", "b"]
    assert len(module.operations) == 2
    assert all(isinstance(item, BinaryOp) for item in module.operations)
    assert module.output.names == ("x", "y")


def test_input_sample_uses_freshness_cursor_without_changing_old_expression() -> None:
    c = Circuit("fresh")
    x = c.input("x")
    x0 = x
    c.tick(3)
    x3 = x.sample()
    combined = x0 + x3
    c.output("combined", combined)

    assert isinstance(x3.ir, InputSample)
    assert x3.ir.offset == 3
    assert x0.ir is c.build().inputs[0]


def test_whole_vector_source_is_sampleable_but_derived_scalar_is_not() -> None:
    c = Circuit("vector_fresh")
    data = c.signals("data")
    scalar = c.input("scalar")
    derived = scalar + 1
    c.tick(2)

    sampled = data.sample()
    assert isinstance(sampled.ir, VectorInputSample)
    assert sampled.ir.offset == 2
    assert not hasattr(derived, "sample")


def test_python_runtime_branching_on_expr_is_rejected() -> None:
    c = Circuit("branch_error")
    x = c.input("x")
    with pytest.raises(CircuitBuildError, match="condition.select"):
        bool(x > 0)


def test_vector_state_primitives_preserve_strict_elaboration_order() -> None:
    c = Circuit("state")
    data = c.signals("data")
    clear = c.input("clear")
    set_signal = c.input("set")

    acc = c.accumulator("acc")
    before = acc.value
    acc.add(data)
    acc.clear(when=clear)
    after = acc.value

    freeze = c.freeze("freeze")
    freeze.set(data, when=set_signal)
    held = freeze.value

    c.output("before", before)
    c.output("after", after)
    c.output("held", held)
    module = c.build()

    assert [type(item) for item in module.state_operations] == [
        AccumulatorAdd,
        AccumulatorClear,
        FreezeSet,
    ]
    acc_ops = [item for item in module.state_operations if item.register.name == "acc"]
    assert [item.order for item in acc_ops] == [1, 2]
    assert isinstance(before.ir, VectorRegisterRead)
    assert isinstance(after.ir, VectorRegisterRead)
    assert before.ir.order == 0
    assert after.ir.order == 3
    assert held.ir.order == 1


def test_tick_until_is_monotone() -> None:
    c = Circuit("time")
    c.tick(2)
    c.tick_until(5)
    assert c.now.offset == 5
    with pytest.raises(CircuitBuildError, match="current freshness 5"):
        c.tick_until(4)
