"""Common-subexpression elimination for immutable scalar stateless values."""

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


def eliminate_common_subexpressions(module: CircuitModule) -> CircuitModule:
    memo: dict[int, ScalarValue] = {}
    interned: dict[tuple[object, ...], ScalarValue] = {}

    def rewrite(value: ScalarValue) -> ScalarValue:
        key: tuple[object, ...]
        cached = memo.get(id(value))
        if cached is not None:
            return cached
        if isinstance(value, (Input, InputSample, Constant, VectorSignal)):
            result: ScalarValue = value
        elif isinstance(value, BinaryOp):
            left = rewrite(value.left)
            right = rewrite(value.right)
            key = ("binary", value.op, id(left), id(right))
            result = interned.setdefault(key, BinaryOp(value.op, left, right, value.name))
        elif isinstance(value, Compare):
            left = rewrite(value.left)
            right = rewrite(value.right)
            key = ("compare", value.op, id(left), id(right))
            result = interned.setdefault(key, Compare(value.op, left, right, value.name))
        elif isinstance(value, Select):
            condition = rewrite(value.condition)
            when_true = rewrite(value.when_true)
            when_false = rewrite(value.when_false)
            key = ("select", id(condition), id(when_true), id(when_false))
            result = interned.setdefault(key, Select(condition, when_true, when_false, value.name))
        else:  # pragma: no cover
            raise TypeError(value)
        memo[id(value)] = result
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
        module.event_inputs,
        module.event_state_operations,
        module.sample_on_crossings,
    )
    return CircuitModule(
        provisional.name,
        provisional.inputs,
        reachable_operations(provisional),
        provisional.output,
        provisional.vector_inputs,
        provisional.state_registers,
        provisional.state_operations,
        provisional.event_inputs,
        provisional.event_state_operations,
        provisional.sample_on_crossings,
    )
