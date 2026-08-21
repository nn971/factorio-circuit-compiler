"""Canonical-IR extraction for temporal technology mapping.

The stateless extractor maps one Level occurrence with caller-supplied output phases. The periodic
output-cone extractor can externalize logical register occurrences as stable boundary sources for a
caller-prescribed period. The full periodic-state extractor is stricter: register reads remain
unresolved ``MappingStateRead`` values and state transitions carry no physical consume phase, so a
future state-cell candidate—not the established state-timing analyzer—must own that timing.
"""

from __future__ import annotations

from factorio_circuit.ir.semantic import (
    BinaryOp,
    CircuitModule,
    Compare,
    Constant,
    FlowInput,
    FlowInputSample,
    FlowVectorInput,
    FlowVectorInputSample,
    Input,
    InputSample,
    PayloadShape,
    Select,
    VectorBinaryOp,
    VectorConstant,
    VectorFilter,
    VectorInput,
    VectorInputSample,
    VectorScalarOp,
    VectorSelect,
    VectorSignal,
    is_vector_value,
    reject_event_module,
    validate_canonical_module,
)
from factorio_circuit.ir.state import StateTransition, VectorRegisterRead, state_transitions
from factorio_circuit.sampling import SamplingPolicy

from .problem import (
    MappingOperation,
    MappingProblem,
    MappingProblemError,
    MappingSink,
    MappingSource,
    MappingSourceMode,
    MappingStateRead,
    MappingStateTransition,
)

_SCALAR_SOURCES = (FlowInput, Input, FlowInputSample, InputSample, Constant)
_VECTOR_SOURCES = (
    FlowVectorInput,
    VectorInput,
    FlowVectorInputSample,
    VectorInputSample,
    VectorConstant,
)
_OPERATIONS = (
    BinaryOp,
    Compare,
    Select,
    VectorSignal,
    VectorBinaryOp,
    VectorScalarOp,
    VectorFilter,
    VectorSelect,
)


def build_stateless_level_mapping_problem(
    module: CircuitModule,
    *,
    output_phases: tuple[int, ...],
    sampling_policy: SamplingPolicy = SamplingPolicy.BEGINNING_OF_STEP,
) -> MappingProblem:
    """Extract one implementation-neutral stateless Level mapping problem."""

    reject_event_module(module)
    validate_canonical_module(module)
    if module.state_registers or state_transitions(module):
        raise MappingProblemError(
            "stateless mapping extraction supports modules without periodic state only"
        )
    return _build_level_mapping_problem(
        module,
        output_phases=output_phases,
        sampling_policy=sampling_policy,
        period=None,
        externalize_state_reads=False,
        include_state_transitions=False,
    )


def build_periodic_level_mapping_problem(
    module: CircuitModule,
    *,
    period: int,
    output_phases: tuple[int, ...],
    sampling_policy: SamplingPolicy = SamplingPolicy.BEGINNING_OF_STEP,
) -> MappingProblem:
    """Extract output cones with register occurrences externalized as stable boundary sources.

    For a ``VectorRegisterRead`` with logical offset ``k``, this diagnostic abstraction supplies the
    token throughout ``[k*period, (k+1)*period)``. It is useful for mapping a post-update output cone
    against an already prescribed logical cadence, but it is not the eventual recurrence IR: the
    physical phase of a real register read port may depend on the selected state-cell implementation.
    """

    _validate_periodic_request(module, period)
    return _build_level_mapping_problem(
        module,
        output_phases=output_phases,
        sampling_policy=sampling_policy,
        period=period,
        externalize_state_reads=True,
        include_state_transitions=False,
    )


def build_periodic_state_mapping_problem(
    module: CircuitModule,
    *,
    period: int,
    output_phases: tuple[int, ...],
    sampling_policy: SamplingPolicy = SamplingPolicy.BEGINNING_OF_STEP,
) -> MappingProblem:
    """Extract full periodic semantic recurrence obligations without physical state timing.

    ``period`` is a logical cadence constraint. Register occurrences become unresolved
    :class:`MappingStateRead` values: only register identity and logical offset are retained. Every
    canonical periodic ``StateTransition`` contributes a :class:`MappingStateTransition` referencing
    the mapping ids of its value/control cones, but no transition input phase is invented.

    The current joint solver intentionally rejects the resulting stateful problem until state-cell
    implementation candidates provide read/write port timing equations.
    """

    _validate_periodic_request(module, period)
    return _build_level_mapping_problem(
        module,
        output_phases=output_phases,
        sampling_policy=sampling_policy,
        period=period,
        externalize_state_reads=False,
        include_state_transitions=True,
    )


def _validate_periodic_request(module: CircuitModule, period: int) -> None:
    reject_event_module(module)
    validate_canonical_module(module)
    if isinstance(period, bool) or not isinstance(period, int) or period < 1:
        raise MappingProblemError("periodic mapping period must be a positive integer")


def _build_level_mapping_problem(
    module: CircuitModule,
    *,
    output_phases: tuple[int, ...],
    sampling_policy: SamplingPolicy,
    period: int | None,
    externalize_state_reads: bool,
    include_state_transitions: bool,
) -> MappingProblem:
    if len(output_phases) != len(module.output.values):
        raise MappingProblemError("output phase count must match semantic output arity")
    if not output_phases:
        raise MappingProblemError("mapping extraction requires at least one output")
    if any(
        isinstance(phase, bool) or not isinstance(phase, int) or phase < 0
        for phase in output_phases
    ):
        raise MappingProblemError("output phases must be non-negative integers")
    if not isinstance(sampling_policy, SamplingPolicy):
        raise TypeError("sampling_policy must be a SamplingPolicy")
    if include_state_transitions and period is None:
        raise AssertionError("state-transition mapping requires a periodic occurrence coordinate")

    horizon = max(output_phases)
    next_value_id = 1
    source_by_semantic: dict[int, MappingSource] = {}
    state_read_by_semantic: dict[int, MappingStateRead] = {}
    operation_by_semantic: dict[int, MappingOperation] = {}
    sources: list[MappingSource] = []
    state_reads: list[MappingStateRead] = []
    operations: list[MappingOperation] = []

    def take_value_id() -> int:
        nonlocal next_value_id
        result = next_value_id
        next_value_id += 1
        return result

    def state_read_for(value: VectorRegisterRead) -> MappingStateRead:
        cached = state_read_by_semantic.get(id(value))
        if cached is not None:
            return cached
        read = MappingStateRead(
            id=take_value_id(),
            label=_label(value),
            semantic=value,
        )
        state_read_by_semantic[id(value)] = read
        state_reads.append(read)
        return read

    def source_for(value: object) -> MappingSource:
        cached = source_by_semantic.get(id(value))
        if cached is not None:
            return cached

        shape = (
            PayloadShape.VECTOR
            if isinstance(value, VectorRegisterRead) or is_vector_value(value)
            else PayloadShape.SCALAR
        )
        label = _label(value)
        if isinstance(value, (Constant, VectorConstant)):
            mode = MappingSourceMode.STABLE
            start = 0
            end = None
        elif isinstance(value, VectorRegisterRead):
            if period is None or not externalize_state_reads:
                raise MappingProblemError(
                    "unresolved state reads require the full periodic-state mapping extractor"
                )
            if value.offset < 0:
                raise MappingProblemError(
                    "periodic output-cone mapping requires non-negative register-read offsets"
                )
            start = value.offset * period
            end = start + period
            mode = MappingSourceMode.STABLE
        elif isinstance(
            value,
            (FlowInputSample, InputSample, FlowVectorInputSample, VectorInputSample),
        ):
            if period is None:
                if value.offset != 0:
                    raise MappingProblemError(
                        "stateless mapping extraction does not model nonzero logical offsets"
                    )
                start = 0
                observable_end = horizon + 1
            else:
                if value.offset < 0:
                    raise MappingProblemError(
                        "periodic mapping currently requires non-negative input-sample offsets"
                    )
                start = value.offset * period
                observable_end = start + period
            if sampling_policy is SamplingPolicy.ALAP:
                mode = MappingSourceMode.OBSERVABLE
                end = observable_end
            else:
                mode = MappingSourceMode.EXACT
                end = start + 1
        elif isinstance(value, (FlowInput, Input, FlowVectorInput, VectorInput)):
            start = 0
            if sampling_policy is SamplingPolicy.ALAP:
                mode = MappingSourceMode.OBSERVABLE
                end = horizon + 1
            else:
                mode = MappingSourceMode.EXACT
                end = 1
        else:
            scope = "periodic" if period is not None else "stateless"
            raise MappingProblemError(
                f"unsupported {scope} mapping source {type(value).__name__}"
            )

        source = MappingSource(
            id=take_value_id(),
            label=label,
            shape=shape,
            mode=mode,
            semantic=value,
            start_phase=start,
            end_phase_exclusive=end,
        )
        source_by_semantic[id(value)] = source
        sources.append(source)
        return source

    def visit(value: object) -> int:
        if isinstance(value, VectorRegisterRead):
            if include_state_transitions:
                return state_read_for(value).id
            return source_for(value).id
        if isinstance(value, (*_SCALAR_SOURCES, *_VECTOR_SOURCES)):
            return source_for(value).id
        if not isinstance(value, _OPERATIONS):
            scope = "periodic" if period is not None else "stateless"
            raise MappingProblemError(f"unsupported {scope} mapping value {type(value).__name__}")

        cached = operation_by_semantic.get(id(value))
        if cached is not None:
            return cached.id
        operand_values = _children(value)
        operand_ids = tuple(visit(child) for child in operand_values)
        operation = MappingOperation(
            id=take_value_id(),
            label=_label(value),
            shape=PayloadShape.VECTOR if is_vector_value(value) else PayloadShape.SCALAR,
            operands=operand_ids,
            semantic=value,
        )
        operation_by_semantic[id(value)] = operation
        operations.append(operation)
        return operation.id

    output_values = tuple(visit(value) for value in module.output.values)

    transition_specs: list[tuple[StateTransition, int | None, int | None]] = []
    if include_state_transitions:
        for transition in state_transitions(module):
            if transition.trigger is not None:
                raise MappingProblemError(
                    "periodic state mapping cannot contain Event-triggered transitions"
                )
            value_id = visit(transition.value) if transition.value is not None else None
            when_id = visit(transition.when) if transition.when is not None else None
            transition_specs.append((transition, value_id, when_id))

    next_special_id = next_value_id
    sinks = tuple(
        MappingSink(
            id=next_special_id + index,
            label=_output_label(module, index),
            value=value_id,
            phase=output_phases[index],
        )
        for index, value_id in enumerate(output_values)
    )
    transition_base = next_special_id + len(sinks)
    mapped_transitions = tuple(
        MappingStateTransition(
            id=transition_base + index,
            label=_transition_label(transition),
            value=value_id,
            when=when_id,
            semantic=transition,
        )
        for index, (transition, value_id, when_id) in enumerate(transition_specs)
    )

    return MappingProblem(
        horizon=horizon,
        sources=tuple(sources),
        operations=tuple(operations),
        sinks=sinks,
        state_reads=tuple(state_reads),
        state_transitions=mapped_transitions,
    )


def _children(value: object) -> tuple[object, ...]:
    if isinstance(value, (BinaryOp, Compare, VectorBinaryOp)):
        return (value.left, value.right)
    if isinstance(value, Select):
        return (value.condition, value.when_true, value.when_false)
    if isinstance(value, VectorSignal):
        return (value.vector,)
    if isinstance(value, VectorScalarOp):
        return (value.vector, value.scalar)
    if isinstance(value, (VectorFilter, VectorSelect)):
        return (value.vector,)
    raise TypeError(value)


def _label(value: object) -> str:
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name
    if isinstance(value, VectorRegisterRead):
        return f"state {value.register.name}[{value.offset}]"
    if isinstance(value, (FlowInput, Input, FlowVectorInput, VectorInput)):
        return value.name
    if isinstance(
        value,
        (FlowInputSample, InputSample, FlowVectorInputSample, VectorInputSample),
    ):
        return value.name or value.source.name
    if isinstance(value, Constant):
        return f"constant {value.value}"
    if isinstance(value, VectorConstant):
        return "vector constant"
    if isinstance(value, BinaryOp):
        return f"binary {value.op}"
    if isinstance(value, Compare):
        return f"compare {value.op}"
    if isinstance(value, Select):
        return "select"
    if isinstance(value, VectorSignal):
        return f"lane {value.signal.name}"
    if isinstance(value, VectorBinaryOp):
        return f"vector {value.op}"
    if isinstance(value, VectorScalarOp):
        return f"vector-scalar {value.op}"
    if isinstance(value, VectorFilter):
        return f"vector-filter {value.op}"
    if isinstance(value, VectorSelect):
        return f"vector-select {value.op}"
    return type(value).__name__


def _output_label(module: CircuitModule, index: int) -> str:
    if module.output.names:
        name = module.output.names[index]
        if name:
            return name
    value = module.output.values[index]
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name
    return f"out{index}"


def _transition_label(transition: StateTransition) -> str:
    return (
        f"state {transition.register.name} {transition.kind} "
        f"offset {transition.logical_offset} order {transition.order}"
    )
