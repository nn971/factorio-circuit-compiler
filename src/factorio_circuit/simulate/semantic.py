"""Reference evaluator for logical scalar streams and current whole-vector state components."""

from __future__ import annotations

from typing import SupportsInt, cast

from factorio_circuit.analysis.state_timing import StateTimingPlan, analyze_state_timing
from factorio_circuit.ir.physical import SignalId
from factorio_circuit.ir.semantic import (
    BinaryOp,
    CircuitModule,
    Compare,
    Constant,
    Input,
    InputSample,
    ScalarValue,
    Select,
    VectorConstant,
    VectorInput,
    VectorInputSample,
    VectorSignal,
    VectorValue,
    reject_event_module,
)
from factorio_circuit.ir.state import (
    AccumulatorAdd,
    AccumulatorClear,
    AccumulatorRegister,
    FreezeRegister,
    FreezeSet,
    VectorRegisterRead,
)
from factorio_circuit.target.factorio.semantics import apply_binary, apply_compare, i32

type SignalMap = dict[SignalId, int]
type LogicalInputRow = dict[str, object]
type LogicalOutput = int | SignalMap


def evaluate(module: CircuitModule, inputs: dict[str, int]) -> tuple[int, ...]:
    """Evaluate one logical tick for a scalar stateless circuit with held external inputs."""

    reject_event_module(module)
    if module.state_registers or module.vector_inputs:
        raise ValueError(
            "single-tick evaluate() is scalar/stateless; use simulate_stream() for state"
        )
    return tuple(_evaluate_value(value, inputs) for value in module.output.values)  # type: ignore[arg-type]


def simulate_stream(
    module: CircuitModule,
    input_stream: list[LogicalInputRow],
    *,
    state_timing: StateTimingPlan | None = None,
) -> list[tuple[LogicalOutput, ...]]:
    """Evaluate logical output streams, including fresh samples and vector state."""

    reject_event_module(module)
    histories: dict[str, list[SignalMap]] = {}
    if module.state_registers:
        _validate_state_startup_model(module)
        timing = state_timing or analyze_state_timing(module)
        histories = _simulate_state_histories(module, input_stream, timing)
    result: list[tuple[LogicalOutput, ...]] = []
    for logical_tick in range(len(input_stream)):
        scalar_memo: dict[tuple[int, int], int] = {}
        row: list[LogicalOutput] = []
        for value in module.output.values:
            if isinstance(
                value, (VectorInput, VectorInputSample, VectorConstant, VectorRegisterRead)
            ):
                row.append(_evaluate_output_vector(value, input_stream, logical_tick, histories))
            else:
                row.append(
                    _evaluate_stream_value(
                        value, input_stream, logical_tick, scalar_memo, histories
                    )
                )
        result.append(tuple(row))
    return result


def _validate_state_startup_model(module: CircuitModule) -> None:
    """Keep zero-initial simulation away from unresolved future external-sample warm-up."""

    def scalar_has_future_sample(value: ScalarValue, seen: set[int] | None = None) -> bool:
        if seen is None:
            seen = set()
        if id(value) in seen:
            return False
        seen.add(id(value))
        if isinstance(value, InputSample):
            return value.offset > 0
        if isinstance(value, (Input, Constant, VectorSignal)):
            return False
        if isinstance(value, (BinaryOp, Compare)):
            return scalar_has_future_sample(value.left, seen) or scalar_has_future_sample(
                value.right, seen
            )
        if isinstance(value, Select):
            return any(
                scalar_has_future_sample(item, seen)
                for item in (value.condition, value.when_true, value.when_false)
            )
        raise TypeError(value)

    for op in module.state_operations:
        if (
            isinstance(op, (AccumulatorAdd, FreezeSet))
            and isinstance(op.value, VectorInputSample)
            and op.value.offset > 0
        ):
            raise ValueError(
                "zero-initial state simulation does not yet define startup/warm-up semantics for "
                "future-sampled vector update sources"
            )
        controls: list[ScalarValue] = []
        if isinstance(op, (AccumulatorAdd, AccumulatorClear, FreezeSet)):
            controls.append(op.when)
        if any(scalar_has_future_sample(control) for control in controls):
            raise ValueError(
                "zero-initial state simulation does not yet define startup/warm-up semantics for "
                "future-sampled state controls"
            )


def _simulate_state_histories(
    module: CircuitModule,
    input_stream: list[LogicalInputRow],
    timing: StateTimingPlan,
) -> dict[str, list[SignalMap]]:
    max_read_offset = max(
        (
            item.read.offset
            for register_timing in timing.registers
            for item in register_timing.reads
        ),
        default=0,
    )
    max_boundary = max(0, len(input_stream) - 1 + max_read_offset)
    histories: dict[str, list[SignalMap]] = {
        register.name: [{}] for register in module.state_registers
    }
    operations = {
        register.name: tuple(op for op in module.state_operations if op.register == register)
        for register in module.state_registers
    }

    # Advance all registers together so cross-register reads at one boundary see the same old state.
    for boundary in range(max_boundary):
        next_states: dict[str, SignalMap] = {}
        for register in module.state_registers:
            current = histories[register.name][boundary]
            register_timing = timing.for_register(register)
            invocation = boundary - register_timing.commit_offset
            if invocation < 0:
                next_states[register.name] = dict(current)
                continue
            if isinstance(register, AccumulatorRegister):
                next_states[register.name] = _step_accumulator(
                    current,
                    operations[register.name],
                    input_stream,
                    invocation,
                    histories,
                )
            elif isinstance(register, FreezeRegister):
                next_states[register.name] = _step_freeze(
                    current,
                    operations[register.name],
                    input_stream,
                    invocation,
                    histories,
                )
            else:  # pragma: no cover
                raise TypeError(register)
        for register in module.state_registers:
            histories[register.name].append(next_states[register.name])
    return histories


def _step_accumulator(
    current: SignalMap,
    operations: tuple[object, ...],
    input_stream: list[LogicalInputRow],
    invocation: int,
    histories: dict[str, list[SignalMap]],
) -> SignalMap:
    adds = [op for op in operations if isinstance(op, AccumulatorAdd)]
    clear = next((op for op in operations if isinstance(op, AccumulatorClear)), None)
    memo: dict[tuple[int, int], int] = {}
    if (
        clear is not None
        and _evaluate_stream_value(clear.when, input_stream, invocation, memo, histories) != 0
    ):
        return {}

    result = dict(current)
    for add in adds:
        if _evaluate_stream_value(add.when, input_stream, invocation, memo, histories) == 0:
            continue
        value = _evaluate_vector_source(add.value, input_stream, invocation, histories)
        for signal, amount in value.items():
            updated = i32(result.get(signal, 0) + amount)
            if updated == 0:
                result.pop(signal, None)
            else:
                result[signal] = updated
    return result


def _step_freeze(
    current: SignalMap,
    operations: tuple[object, ...],
    input_stream: list[LogicalInputRow],
    invocation: int,
    histories: dict[str, list[SignalMap]],
) -> SignalMap:
    spec = next(op for op in operations if isinstance(op, FreezeSet))
    memo: dict[tuple[int, int], int] = {}
    if _evaluate_stream_value(spec.when, input_stream, invocation, memo, histories) == 0:
        return dict(current)
    return _evaluate_vector_source(spec.value, input_stream, invocation, histories)


def _evaluate_output_vector(
    value: VectorValue,
    input_stream: list[LogicalInputRow],
    logical_tick: int,
    histories: dict[str, list[SignalMap]],
) -> SignalMap:
    return _evaluate_vector_source(value, input_stream, logical_tick, histories)


def _evaluate_vector_source(
    value: VectorValue,
    input_stream: list[LogicalInputRow],
    logical_tick: int,
    histories: dict[str, list[SignalMap]],
) -> SignalMap:
    if isinstance(value, VectorInput):
        return _lookup_vector_input(input_stream, logical_tick, value.name)
    if isinstance(value, VectorInputSample):
        return _lookup_vector_input(input_stream, logical_tick + value.offset, value.source.name)
    if isinstance(value, VectorConstant):
        return {signal: i32(amount) for signal, amount in value.signals if i32(amount) != 0}
    if isinstance(value, VectorRegisterRead):
        boundary = logical_tick + value.offset
        history = histories[value.register.name]
        if boundary < 0 or boundary >= len(history):
            raise ValueError(
                f"state read {value.register.name!r} requests unavailable boundary {boundary}"
            )
        return dict(history[boundary])
    raise TypeError(value)


def _evaluate_value(
    value: ScalarValue,
    inputs: dict[str, int],
    memo: dict[int, int] | None = None,
) -> int:
    if memo is None:
        memo = {}
    key = id(value)
    if key in memo:
        return memo[key]
    if isinstance(value, Input):
        result = _lookup_input(inputs, value.name)
    elif isinstance(value, InputSample):
        result = _lookup_input(inputs, value.source.name)
    elif isinstance(value, Constant):
        result = i32(value.value)
    elif isinstance(value, VectorSignal):
        if not isinstance(value.vector, VectorConstant):
            raise ValueError("single-tick scalar evaluation cannot read dynamic vector state")
        result = dict(value.vector.signals).get(value.signal, 0)
    elif isinstance(value, BinaryOp):
        result = apply_binary(
            value.op,
            _evaluate_value(value.left, inputs, memo),
            _evaluate_value(value.right, inputs, memo),
        )
    elif isinstance(value, Compare):
        result = int(
            apply_compare(
                value.op,
                _evaluate_value(value.left, inputs, memo),
                _evaluate_value(value.right, inputs, memo),
            )
        )
    elif isinstance(value, Select):
        branch = (
            value.when_true
            if _evaluate_value(value.condition, inputs, memo) != 0
            else value.when_false
        )
        result = _evaluate_value(branch, inputs, memo)
    else:  # pragma: no cover
        raise TypeError(value)
    memo[key] = result
    return result


def _evaluate_stream_value(
    value: ScalarValue,
    input_stream: list[LogicalInputRow],
    logical_tick: int,
    memo: dict[tuple[int, int], int],
    histories: dict[str, list[SignalMap]] | None = None,
) -> int:
    key = (id(value), logical_tick)
    cached = memo.get(key)
    if cached is not None:
        return cached
    if isinstance(value, Input):
        result = _lookup_stream_input(input_stream, logical_tick, value.name)
    elif isinstance(value, InputSample):
        result = _lookup_stream_input(input_stream, logical_tick + value.offset, value.source.name)
    elif isinstance(value, Constant):
        result = i32(value.value)
    elif isinstance(value, VectorSignal):
        if histories is None:
            raise ValueError("vector signal extraction requires vector/state stream context")
        vector = _evaluate_vector_source(value.vector, input_stream, logical_tick, histories)
        result = vector.get(value.signal, 0)
    elif isinstance(value, BinaryOp):
        result = apply_binary(
            value.op,
            _evaluate_stream_value(value.left, input_stream, logical_tick, memo, histories),
            _evaluate_stream_value(value.right, input_stream, logical_tick, memo, histories),
        )
    elif isinstance(value, Compare):
        result = int(
            apply_compare(
                value.op,
                _evaluate_stream_value(value.left, input_stream, logical_tick, memo, histories),
                _evaluate_stream_value(value.right, input_stream, logical_tick, memo, histories),
            )
        )
    elif isinstance(value, Select):
        branch = (
            value.when_true
            if _evaluate_stream_value(value.condition, input_stream, logical_tick, memo, histories)
            != 0
            else value.when_false
        )
        result = _evaluate_stream_value(branch, input_stream, logical_tick, memo, histories)
    else:  # pragma: no cover
        raise TypeError(value)
    memo[key] = result
    return result


def _lookup_input(inputs: dict[str, int], name: str) -> int:
    try:
        return i32(inputs[name])
    except KeyError as exc:
        raise ValueError(f"missing input {name!r}") from exc


def _lookup_stream_input(input_stream: list[LogicalInputRow], tick: int, name: str) -> int:
    if tick < 0 or tick >= len(input_stream):
        return 0
    raw = input_stream[tick].get(name, 0)
    if isinstance(raw, dict):
        raise ValueError(f"scalar input {name!r} received a signal map")
    return i32(int(cast(SupportsInt, raw)))


def _lookup_vector_input(input_stream: list[LogicalInputRow], tick: int, name: str) -> SignalMap:
    if tick < 0 or tick >= len(input_stream):
        return {}
    raw = input_stream[tick].get(name, {})
    if not isinstance(raw, dict):
        raise ValueError(f"vector input {name!r} expects a signal map")
    result: SignalMap = {}
    for signal, value in raw.items():
        if not isinstance(signal, SignalId):
            raise ValueError("semantic vector simulation expects SignalId keys")
        amount = i32(int(value))
        if amount != 0:
            result[signal] = amount
    return result
