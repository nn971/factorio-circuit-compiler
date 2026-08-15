"""Small structural clock algebra used by explicit clock-normalization primitives."""

from __future__ import annotations

from dataclasses import dataclass

from factorio_circuit.events import EventCausalityError, EventCrossingError
from factorio_circuit.ir.semantic import (
    BinaryOp,
    ClockProvenance,
    Compare,
    Constant,
    EventInput,
    EventScalarFlow,
    EventVectorFlow,
    Flow,
    PayloadShape,
    SampleOn,
    ScalarValue,
    Select,
    TemporalModality,
    VectorBinaryOp,
    VectorFilter,
    VectorRegisterRead,
    VectorScalarOp,
    VectorSelect,
    VectorSignal,
    validate_expression_flow,
)


def _contains_state_read(value: object, seen: set[int] | None = None) -> bool:
    if seen is None:
        seen = set()
    if id(value) in seen:
        return False
    seen.add(id(value))
    if isinstance(value, VectorRegisterRead):
        return True
    if isinstance(value, SampleOn):
        return _contains_state_read(value.source, seen)
    for field_name in (
        "left",
        "right",
        "condition",
        "when_true",
        "when_false",
        "vector",
        "scalar",
    ):
        child = getattr(value, field_name, None)
        if child is not None and _contains_state_read(child, seen):
            return True
    return False


def _event_offsets(value: object, seen: set[int] | None = None) -> set[int]:
    if seen is None:
        seen = set()
    if id(value) in seen:
        return set()
    seen.add(id(value))
    flow = getattr(value, "flow", None)
    offsets: set[int] = set()
    if isinstance(flow, Flow) and flow.modality is TemporalModality.EVENT:
        offsets.add(flow.logical_offset)
    if isinstance(value, SampleOn):
        offsets.update(_event_offsets(value.source, seen))
    for field_name in (
        "left",
        "right",
        "condition",
        "when_true",
        "when_false",
        "vector",
        "scalar",
    ):
        child = getattr(value, field_name, None)
        if child is not None:
            offsets.update(_event_offsets(child, seen))
    return offsets


@dataclass(frozen=True, slots=True)
class GateClock(EventInput):
    """A unit-valued derived Event source obtained by filtering a parent clock.

    ``predicate`` is evaluated atomically at the current parent occurrence.  The derived source is a
    subclock, so it conservatively inherits the parent's minimum-separation guarantee.  This first
    executable slice permits Event-local expressions and explicit state-free ``SampleOn`` values;
    state-dependent clock gating is deferred until stateful clock-normalization semantics exist.
    """

    parent: EventInput
    predicate: ScalarValue

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("gated clock name must be non-empty")
        if self.payload_shape is not PayloadShape.SCALAR:
            raise ValueError("GateClock is a unit-valued scalar Event source")
        if not isinstance(self.parent, EventInput):
            raise ValueError("GateClock parent must be an Event source")
        if self.clock.provenance is not ClockProvenance.DERIVED:
            raise ValueError("GateClock clock must have DERIVED provenance")
        if self.clock.contract != self.parent.clock.contract:
            raise ValueError("GateClock must inherit its parent ClockContract")

        facts = validate_expression_flow(self.predicate, PayloadShape.SCALAR)
        if facts.modality is TemporalModality.LEVEL:
            raise EventCrossingError(
                "GateClock predicates cannot implicitly read a Level; use SampleOn on the parent clock"
            )
        if facts.modality is TemporalModality.EVENT and facts.clock != self.parent.clock:
            raise EventCausalityError("GateClock predicate must use the parent occurrence clock")
        if _contains_state_read(self.predicate):
            raise EventCrossingError(
                "state-dependent GateClock predicates are not implemented; use state on the parent "
                "clock instead"
            )
        offsets = _event_offsets(self.predicate)
        if any(offset != 0 for offset in offsets):
            raise EventCausalityError(
                "GateClock predicate must observe the current parent occurrence; .step() is not a "
                "clock filter"
            )


# Keep imported expression classes visible to static analyzers that validate the recursive helper
# coverage as the semantic scalar/vector vocabulary evolves.
_GATE_EXPRESSION_TYPES = (
    BinaryOp,
    Compare,
    Constant,
    EventScalarFlow,
    EventVectorFlow,
    Select,
    VectorBinaryOp,
    VectorFilter,
    VectorScalarOp,
    VectorSelect,
    VectorSignal,
)
