"""Narrow canonical-IR extraction for the first temporal mapping milestone.

The extractor intentionally supports only one stateless Level occurrence with caller-supplied
physical output phases. It does not reuse the old state-timing windows, because those already encode
one particular implementation's Factorio latency. Periodic state, logical reindexing across
occurrences, and Event clocks enter later milestones once their implementation-neutral constraints
are represented directly.
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
from factorio_circuit.ir.state import VectorRegisterRead, state_transitions
from factorio_circuit.sampling import SamplingPolicy

from .problem import (
    MappingOperation,
    MappingProblem,
    MappingProblemError,
    MappingSink,
    MappingSource,
    MappingSourceMode,
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
    """Extract one implementation-neutral stateless Level mapping problem.

    ``output_phases`` are explicit target constraints supplied by the caller; they are not inferred
    from the current ordinary lowering. Under ``ALAP`` an offset-zero external Level source is
    modeled as physically observable through the mapping horizon. Constants remain stable.
    """

    reject_event_module(module)
    validate_canonical_module(module)
    if module.state_registers or state_transitions(module):
        raise MappingProblemError(
            "first-milestone mapping extraction supports stateless Level modules only"
        )
    if len(output_phases) != len(module.output.values):
        raise MappingProblemError("output phase count must match semantic output arity")
    if not output_phases:
        raise MappingProblemError("stateless mapping extraction requires at least one output")
    if any(
        isinstance(phase, bool) or not isinstance(phase, int) or phase < 0
        for phase in output_phases
    ):
        raise MappingProblemError("output phases must be non-negative integers")
    if not isinstance(sampling_policy, SamplingPolicy):
        raise TypeError("sampling_policy must be a SamplingPolicy")

    horizon = max(output_phases)
    next_value_id = 1
    source_by_semantic: dict[int, MappingSource] = {}
    operation_by_semantic: dict[int, MappingOperation] = {}
    sources: list[MappingSource] = []
    operations: list[MappingOperation] = []

    def take_value_id() -> int:
        nonlocal next_value_id
        result = next_value_id
        next_value_id += 1
        return result

    def source_for(value: object) -> MappingSource:
        cached = source_by_semantic.get(id(value))
        if cached is not None:
            return cached

        shape = PayloadShape.VECTOR if is_vector_value(value) else PayloadShape.SCALAR
        label = _label(value)
        if isinstance(value, (Constant, VectorConstant)):
            mode = MappingSourceMode.STABLE
            start = 0
            end = None
        elif isinstance(
            value,
            (FlowInputSample, InputSample, FlowVectorInputSample, VectorInputSample),
        ):
            if value.offset != 0:
                raise MappingProblemError(
                    "first-milestone mapping extraction does not yet model nonzero logical offsets"
                )
            start = 0
            if sampling_policy is SamplingPolicy.ALAP:
                mode = MappingSourceMode.OBSERVABLE
                end = horizon + 1
            else:
                mode = MappingSourceMode.EXACT
                end = 1
        elif isinstance(value, (FlowInput, Input, FlowVectorInput, VectorInput)):
            start = 0
            if sampling_policy is SamplingPolicy.ALAP:
                mode = MappingSourceMode.OBSERVABLE
                end = horizon + 1
            else:
                mode = MappingSourceMode.EXACT
                end = 1
        else:
            raise MappingProblemError(
                f"unsupported stateless mapping source {type(value).__name__}"
            )

        source = MappingSource(
            id=take_value_id(),
            label=label,
            shape=shape,
            mode=mode,
            start_phase=start,
            end_phase_exclusive=end,
        )
        source_by_semantic[id(value)] = source
        sources.append(source)
        return source

    def visit(value: object) -> int:
        if isinstance(value, VectorRegisterRead):
            raise MappingProblemError(
                "first-milestone mapping extraction does not yet model state reads"
            )
        if isinstance(value, (*_SCALAR_SOURCES, *_VECTOR_SOURCES)):
            return source_for(value).id
        if not isinstance(value, _OPERATIONS):
            raise MappingProblemError(
                f"unsupported stateless mapping value {type(value).__name__}"
            )

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
    sinks = tuple(
        MappingSink(
            id=next_value_id + index,
            label=_output_label(module, index),
            value=value_id,
            phase=output_phases[index],
        )
        for index, value_id in enumerate(output_values)
    )
    return MappingProblem(
        horizon=horizon,
        sources=tuple(sources),
        operations=tuple(operations),
        sinks=sinks,
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
