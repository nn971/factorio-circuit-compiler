"""Reference evaluator for logical scalar streams and current whole-vector state components."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import SupportsInt, cast

from factorio_circuit.analysis.state_timing import (
    StateTimingPlan,
    analyze_normalized_state_timing,
)
from factorio_circuit.ir.physical import SignalId
from factorio_circuit.ir.semantic import (
    BinaryOp,
    CircuitModule,
    Compare,
    Constant,
    EventScalarFlow,
    EventVectorFlow,
    Input,
    InputSample,
    SampleOn,
    ScalarValue,
    Select,
    VectorInput,
    VectorInputSample,
    VectorSignal,
    VectorValue,
    is_vector_value,
    reject_event_module,
    validate_canonical_module,
)
from factorio_circuit.ir.state import (
    StateRegister,
    state_transitions,
)
from factorio_circuit.lowering.frontend_to_ir import normalize_module
from factorio_circuit.simulate.kernel import (
    EvaluationContext,
    TimestampReaction,
    evaluate_scalar,
    evaluate_vector,
    run_reaction_kernel,
)
from factorio_circuit.target.factorio.semantics import i32

type SignalMap = dict[SignalId, int]
type LogicalInputRow = dict[str, object]
type LogicalOutput = int | SignalMap


class _LevelEvaluationContext(EvaluationContext):
    def __init__(
        self,
        input_stream: list[LogicalInputRow],
        logical_tick: int = 0,
        histories: dict[str, list[SignalMap]] | None = None,
    ) -> None:
        self.input_stream = input_stream
        self.logical_tick = logical_tick
        self.histories = histories

    def scalar_input(self, source: Input, offset: int) -> int:
        return _lookup_stream_input(self.input_stream, self.logical_tick + offset, source.name)

    def vector_input(self, source: VectorInput, offset: int) -> SignalMap:
        return _lookup_vector_input(self.input_stream, self.logical_tick + offset, source.name)

    def state_vector(self, register: StateRegister, offset: int) -> SignalMap:
        if self.histories is None:
            raise ValueError("state reads require stream simulation context")
        boundary = self.logical_tick + offset
        history = self.histories[register.name]
        if boundary < 0 or boundary >= len(history):
            raise ValueError(
                f"state read {register.name!r} requests unavailable boundary {boundary}"
            )
        return dict(history[boundary])

    def event_scalar(self, source: EventScalarFlow) -> int:
        raise ValueError("Event values are not valid on the Level simulation route")

    def event_vector(self, source: EventVectorFlow) -> SignalMap:
        raise ValueError("Event values are not valid on the Level simulation route")

    def sample_on_scalar(self, source: SampleOn) -> int:
        raise ValueError("SampleOn values are not valid on the Level simulation route")

    def sample_on_vector(self, source: SampleOn) -> SignalMap:
        raise ValueError("SampleOn values are not valid on the Level simulation route")

    def active_event_vector(self) -> SignalMap:
        raise ValueError("Event values are not valid on the Level simulation route")


def evaluate(module: CircuitModule, inputs: dict[str, int]) -> tuple[int, ...]:
    """Evaluate one logical tick for a scalar stateless circuit with held external inputs."""

    reject_event_module(module)
    module = normalize_module(module)
    return evaluate_normalized(module, inputs)


def evaluate_normalized(module: CircuitModule, inputs: dict[str, int]) -> tuple[int, ...]:
    """Evaluate a module that already satisfies the canonical Level invariant."""

    reject_event_module(module)
    validate_canonical_module(module)
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
    module = normalize_module(module)
    return simulate_normalized_stream(module, input_stream, state_timing=state_timing)


def simulate_normalized_stream(
    module: CircuitModule,
    input_stream: list[LogicalInputRow],
    *,
    state_timing: StateTimingPlan | None = None,
) -> list[tuple[LogicalOutput, ...]]:
    """Simulate a module that already satisfies the canonical Level invariant."""

    reject_event_module(module)
    validate_canonical_module(module)
    histories: dict[str, list[SignalMap]] = {}
    if module.state_registers:
        _validate_state_startup_model(module)
        timing = state_timing or analyze_normalized_state_timing(module)
        histories = _simulate_state_histories(module, input_stream, timing)
    result: list[tuple[LogicalOutput, ...]] = []
    for logical_tick in range(len(input_stream)):
        scalar_memo: dict[tuple[int, int], int] = {}
        row: list[LogicalOutput] = []
        for value in module.output.values:
            if is_vector_value(value):
                row.append(
                    _evaluate_output_vector(
                        cast(VectorValue, value), input_stream, logical_tick, histories
                    )
                )
            else:
                row.append(
                    _evaluate_stream_value(
                        cast(ScalarValue, value),
                        input_stream,
                        logical_tick,
                        scalar_memo,
                        histories,
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

    for transition in state_transitions(module):
        if transition.trigger is not None:
            continue
        if (
            transition.kind in {"add", "set"}
            and isinstance(transition.value, VectorInputSample)
            and transition.value.offset > 0
        ):
            raise ValueError(
                "zero-initial state simulation does not yet define startup/warm-up semantics for "
                "future-sampled vector update sources"
            )
        controls: list[ScalarValue] = []
        if transition.kind in {"add", "clear", "set"} and transition.when is not None:
            controls.append(transition.when)
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
    transitions = {
        register.name: tuple(
            transition
            for transition in state_transitions(module)
            if transition.register == register and transition.trigger is None
        )
        for register in module.state_registers
    }

    def batches() -> Iterable[tuple[int, Sequence[tuple[int, object, object]]]]:
        for boundary in range(max_boundary):
            entries: list[tuple[int, object, object]] = []
            for index, register in enumerate(module.state_registers):
                invocation = boundary - timing.for_register(register).commit_offset
                if invocation >= 0:
                    entries.append((index, register, invocation))
            yield boundary, tuple(entries)

    def level_snapshot(boundary: int) -> Mapping[str, object]:
        if 0 <= boundary < len(input_stream):
            return input_stream[boundary]
        return {}

    def context_factory(
        _level_row: Mapping[str, object],
        _before: dict[str, SignalMap],
        _source: object,
        invocation: object,
    ) -> EvaluationContext:
        return _LevelEvaluationContext(input_stream, int(cast(int, invocation)), histories)

    def after_frame(frame: TimestampReaction) -> None:
        for register in module.state_registers:
            histories[register.name].append(dict(frame.state_after[register.name]))

    run_reaction_kernel(
        batches(),
        level_snapshot,
        {register.name: {} for register in module.state_registers},
        lambda source, _invocation: transitions[cast(StateRegister, source).name],
        context_factory,
        after_frame,
    )
    return histories


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
    return evaluate_vector(
        value,
        _LevelEvaluationContext(input_stream, logical_tick, histories),
    )


def _evaluate_value(
    value: ScalarValue,
    inputs: dict[str, int],
    memo: dict[int, int] | None = None,
) -> int:
    row: LogicalInputRow = dict(inputs)
    return evaluate_scalar(value, _LevelEvaluationContext([row]))


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
    result = evaluate_scalar(
        value,
        _LevelEvaluationContext(input_stream, logical_tick, histories),
    )
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
