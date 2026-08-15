"""Pure logical reindexing for frontend Level expressions.

The public ``Expr.step(n)`` and ``SignalsExpr.step(n)`` APIs use these helpers to refer to the same
logical computation at a later occurrence of its clock.  Reindexing changes only logical occurrence
offsets; it never inserts state or a physical delay.

The current production compiler still lowers only Level flows.  Event occurrence reindexing is kept
out of this compatibility slice until Event source wrappers accept nonzero occurrence offsets.
"""

from __future__ import annotations

from dataclasses import replace

from factorio_circuit.ir.semantic import (
    BinaryOp,
    Compare,
    Constant,
    EventScalarFlow,
    EventVectorFlow,
    Flow,
    FlowInput,
    FlowInputSample,
    FlowVectorInput,
    FlowVectorInputSample,
    FlowVectorRegisterRead,
    Input,
    InputSample,
    SampleOn,
    ScalarValue,
    Select,
    TemporalModality,
    VectorBinaryOp,
    VectorConstant,
    VectorFilter,
    VectorInput,
    VectorInputSample,
    VectorScalarOp,
    VectorSelect,
    VectorSignal,
    VectorValue,
)
from factorio_circuit.ir.state import VectorRegisterRead


class FlowStepError(ValueError):
    """Raised when a frontend value cannot be logically reindexed."""


def validate_step_count(n: int) -> None:
    """Validate the non-negative logical displacement accepted by ``.step``."""

    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise FlowStepError("step(n) requires a non-negative integer")


def _shift_flow(flow: Flow | None, n: int) -> Flow | None:
    if flow is None:
        return None
    if flow.modality is TemporalModality.EVENT:
        raise FlowStepError(
            "flow-local step() for Event values is not available until Event occurrence offsets "
            "enter the canonical Event source representation"
        )
    return replace(flow, logical_offset=flow.logical_offset + n)


def _reject_event(value: object) -> None:
    if isinstance(value, (EventScalarFlow, EventVectorFlow, SampleOn)):
        raise FlowStepError(
            "flow-local step() for Event values is not available until Event occurrence offsets "
            "enter the canonical Event source representation"
        )
    flow = getattr(value, "flow", None)
    if isinstance(flow, Flow) and flow.modality is TemporalModality.EVENT:
        raise FlowStepError(
            "flow-local step() for Event values is not available until Event occurrence offsets "
            "enter the canonical Event source representation"
        )


def reindex_scalar(value: ScalarValue, n: int = 1) -> ScalarValue:
    """Return ``value`` at logical occurrence offset ``+n``.

    This operation is structural: Level leaves acquire a later logical offset and ordinary derived
    expressions are rebuilt over those shifted leaves.  No state primitive is introduced.
    """

    validate_step_count(n)
    if n == 0:
        return value
    _reject_event(value)

    if isinstance(value, FlowInputSample):
        return FlowInputSample(
            source=value.source,
            offset=value.offset + n,
            name=value.name,
            flow=_shift_flow(value.flow, n),
        )
    if isinstance(value, FlowInput):
        return FlowInputSample(
            source=value.source,
            offset=value.flow.logical_offset + n,
            name=value.name,
            flow=_shift_flow(value.flow, n),
        )
    if isinstance(value, InputSample):
        return InputSample(value.source, value.offset + n, value.name)
    if type(value) is Input:
        return InputSample(value, n)
    if isinstance(value, Constant):
        # Constants are occurrence-invariant.  Their surrounding expression carries the shift.
        return value
    if isinstance(value, BinaryOp):
        return BinaryOp(
            value.op,
            reindex_scalar(value.left, n),
            reindex_scalar(value.right, n),
            value.name,
            _shift_flow(value.flow, n),
        )
    if isinstance(value, Compare):
        return Compare(
            value.op,
            reindex_scalar(value.left, n),
            reindex_scalar(value.right, n),
            value.name,
            _shift_flow(value.flow, n),
        )
    if isinstance(value, Select):
        return Select(
            reindex_scalar(value.condition, n),
            reindex_scalar(value.when_true, n),
            reindex_scalar(value.when_false, n),
            value.name,
            _shift_flow(value.flow, n),
        )
    if isinstance(value, VectorSignal):
        return VectorSignal(
            reindex_vector(value.vector, n),
            value.signal,
            value.name,
            _shift_flow(value.flow, n),
        )
    raise FlowStepError(f"unsupported scalar flow value {type(value).__name__}")


def reindex_vector(value: VectorValue, n: int = 1) -> VectorValue:
    """Return a whole-vector Level expression at logical occurrence offset ``+n``."""

    validate_step_count(n)
    if n == 0:
        return value
    _reject_event(value)

    if isinstance(value, FlowVectorInputSample):
        return FlowVectorInputSample(
            source=value.source,
            offset=value.offset + n,
            name=value.name,
            flow=_shift_flow(value.flow, n),
        )
    if isinstance(value, FlowVectorInput):
        return FlowVectorInputSample(
            source=value.source,
            offset=value.flow.logical_offset + n,
            name=value.name,
            flow=_shift_flow(value.flow, n),
        )
    if isinstance(value, VectorInputSample):
        return VectorInputSample(value.source, value.offset + n, value.name)
    if type(value) is VectorInput:
        return VectorInputSample(value, n)
    if isinstance(value, FlowVectorRegisterRead):
        return FlowVectorRegisterRead(
            register=value.register,
            offset=value.offset + n,
            order=value.order,
            name=value.name,
            flow=_shift_flow(value.flow, n),
        )
    if type(value) is VectorRegisterRead:
        return VectorRegisterRead(
            register=value.register,
            offset=value.offset + n,
            order=value.order,
            name=value.name,
        )
    if isinstance(value, VectorConstant):
        return value
    if isinstance(value, VectorBinaryOp):
        return VectorBinaryOp(
            value.op,
            reindex_vector(value.left, n),
            reindex_vector(value.right, n),
            _shift_flow(value.flow, n),
        )
    if isinstance(value, VectorScalarOp):
        return VectorScalarOp(
            value.op,
            reindex_vector(value.vector, n),
            reindex_scalar(value.scalar, n),
            _shift_flow(value.flow, n),
        )
    if isinstance(value, VectorSelect):
        return VectorSelect(
            value.op,
            reindex_vector(value.vector, n),
            value.right,
            select_max=value.select_max,
            index=value.index,
            flow=_shift_flow(value.flow, n),
        )
    if isinstance(value, VectorFilter):
        return VectorFilter(
            value.op,
            reindex_vector(value.vector, n),
            value.right,
            _shift_flow(value.flow, n),
        )
    raise FlowStepError(f"unsupported vector flow value {type(value).__name__}")
