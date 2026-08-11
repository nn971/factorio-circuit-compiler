"""Small semantics-preserving simplifications for scalar stateless logic."""

from __future__ import annotations

from factorio_circuit.ir.semantic import (
    BinaryOp,
    CircuitModule,
    Compare,
    Constant,
    Input,
    InputSample,
    ReturnValue,
    ScalarValue,
    Select,
    VectorConstant,
    VectorInput,
    VectorInputSample,
    VectorSignal,
    reachable_operations,
)
from factorio_circuit.ir.state import (
    AccumulatorAdd,
    AccumulatorClear,
    FreezeSet,
    StateOperation,
    VectorRegisterRead,
)
from factorio_circuit.target.factorio.semantics import apply_binary, apply_compare, i32


def simplify_module(module: CircuitModule) -> CircuitModule:
    memo: dict[int, ScalarValue] = {}

    def rewrite(value: ScalarValue) -> ScalarValue:
        key = id(value)
        if key in memo:
            return memo[key]
        if isinstance(value, (Input, InputSample, Constant, VectorSignal)):
            result: ScalarValue = value
        elif isinstance(value, BinaryOp):
            result = _simplify_binary(value.op, rewrite(value.left), rewrite(value.right), value.name)
        elif isinstance(value, Compare):
            left = rewrite(value.left)
            right = rewrite(value.right)
            if isinstance(left, Constant) and isinstance(right, Constant):
                result = Constant(int(apply_compare(value.op, left.value, right.value)), value.name)
            else:
                result = Compare(value.op, left, right, value.name)
        elif isinstance(value, Select):
            condition = rewrite(value.condition)
            when_true = rewrite(value.when_true)
            when_false = rewrite(value.when_false)
            if isinstance(condition, Constant):
                result = when_true if i32(condition.value) != 0 else when_false
            elif when_true == when_false:
                result = when_true
            else:
                result = Select(condition, when_true, when_false, value.name)
        else:  # pragma: no cover
            raise TypeError(value)
        memo[key] = result
        return result

    outputs = tuple(
        value
        if isinstance(value, (VectorInput, VectorInputSample, VectorConstant, VectorRegisterRead))
        else rewrite(value)
        for value in module.output.values
    )
    state_ops: list[StateOperation] = []
    for op in module.state_operations:
        if isinstance(op, AccumulatorAdd):
            state_ops.append(AccumulatorAdd(op.register, op.value, rewrite(op.when), op.order))
        elif isinstance(op, AccumulatorClear):
            state_ops.append(AccumulatorClear(op.register, rewrite(op.when), op.order))
        elif isinstance(op, FreezeSet):
            state_ops.append(FreezeSet(op.register, op.value, rewrite(op.when), op.order))
        else:
            state_ops.append(op)

    provisional = CircuitModule(
        module.name,
        module.inputs,
        (),
        ReturnValue(outputs, module.output.names),
        module.vector_inputs,
        module.state_registers,
        tuple(state_ops),
    )
    return CircuitModule(
        provisional.name,
        provisional.inputs,
        reachable_operations(provisional),
        provisional.output,
        provisional.vector_inputs,
        provisional.state_registers,
        provisional.state_operations,
    )


def _simplify_binary(op: str, left: ScalarValue, right: ScalarValue, name: str | None) -> ScalarValue:
    if isinstance(left, Constant) and isinstance(right, Constant):
        return Constant(apply_binary(op, left.value, right.value), name)
    if isinstance(right, Constant):
        rv = i32(right.value)
        if op in {"+", "-", "|", "^", "<<", ">>"} and rv == 0:
            return left
        if op in {"*", "/", "//", "**"} and rv == 1:
            return left
        if op in {"*", "&"} and rv == 0:
            return Constant(0, name)
    if isinstance(left, Constant):
        lv = i32(left.value)
        if op in {"+", "|", "^"} and lv == 0:
            return right
        if op == "*" and lv == 1:
            return right
        if op in {"*", "&"} and lv == 0:
            return Constant(0, name)
    return BinaryOp(op, left, right, name)
