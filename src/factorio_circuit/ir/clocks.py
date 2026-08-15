"""Small structural clock algebra used by explicit clock-normalization primitives."""

from __future__ import annotations

from dataclasses import dataclass

from factorio_circuit.events import EventCausalityError, EventCrossingError
from factorio_circuit.ir.semantic import (
    Clock,
    ClockProvenance,
    EventInput,
    Flow,
    PayloadShape,
    SampleOn,
    ScalarValue,
    TemporalModality,
    validate_expression_flow,
)
from factorio_circuit.ir.state import AccumulatorRegister, VectorRegisterRead


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

    ``predicate`` is evaluated atomically at the current parent occurrence. The derived source is a
    subclock, so it conservatively inherits the parent's minimum-separation guarantee. This first
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
        if not isinstance(self.clock, Clock):
            raise ValueError("GateClock clock must be a Clock")
        if self.clock.provenance is not ClockProvenance.DERIVED:
            raise ValueError("GateClock clock must have DERIVED provenance")
        if self.clock.contract != self.parent.clock.contract:
            raise ValueError("GateClock must inherit its parent ClockContract")

        facts = validate_expression_flow(self.predicate, PayloadShape.SCALAR)
        if facts.modality is TemporalModality.LEVEL:
            raise EventCrossingError(
                "GateClock predicates cannot implicitly read a Level; "
                "use SampleOn on the parent clock"
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


@dataclass(frozen=True, slots=True)
class EventMerge(EventInput):
    """An additive union of same-shaped Event sources.

    Each parent occurrence contributes its payload to the merged stream. Parent occurrences at the
    same physical timestamp coalesce into one merged occurrence whose payload is the sum of the
    contributing payloads. With two or more distinct parents, unrelated activations may interleave
    arbitrarily closely, so this milestone uses the conservative one-tick spacing contract.
    """

    parents: tuple[EventInput, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("merged Event name must be non-empty")
        if not isinstance(self.clock, Clock):
            raise ValueError("EventMerge clock must be a Clock")
        if self.clock.provenance is not ClockProvenance.DERIVED:
            raise ValueError("EventMerge clock must have DERIVED provenance")
        if len(self.parents) < 2:
            raise ValueError("EventMerge requires at least two distinct parents")
        if len(set(self.parents)) != len(self.parents):
            raise ValueError("EventMerge parents must be unique")
        if any(not isinstance(parent, EventInput) for parent in self.parents):
            raise ValueError("EventMerge parents must be Event sources")
        if any(parent.payload_shape is not self.payload_shape for parent in self.parents):
            raise ValueError("EventMerge parents must have one payload shape")
        if self.clock.guaranteed_min_separation != 1:
            raise ValueError(
                "EventMerge of distinct parents must use the conservative 1-tick bound"
            )


@dataclass(frozen=True, slots=True)
class SumInto(EventInput):
    """Stateful additive bridge from a vector Event source onto a target Event clock.

    The bridge emits exactly one vector occurrence for every target occurrence. Its payload is the
    Factorio-i32 sum of source payloads in the interval ``(previous_target, current_target]``. The
    associated accumulator register makes the history requirement explicit in semantic state even
    though reference simulation can synthesize the same payload directly from deterministic test
    schedules.
    """

    source: EventInput
    target: EventInput
    register: AccumulatorRegister

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("SumInto name must be non-empty")
        if self.payload_shape is not PayloadShape.VECTOR:
            raise ValueError("SumInto is currently a packed vector Event bridge")
        if not isinstance(self.source, EventInput) or not isinstance(self.target, EventInput):
            raise ValueError("SumInto source and target must be Event sources")
        if self.source.payload_shape is not PayloadShape.VECTOR:
            raise EventCrossingError("SumInto source must carry a vector payload")
        if not isinstance(self.register, AccumulatorRegister):
            raise ValueError("SumInto requires an AccumulatorRegister")
        if self.source.clock == self.target.clock:
            raise EventCrossingError(
                "SumInto requires distinct source and target clocks; use the Event value directly "
                "when no re-clocking is needed"
            )
        if self.clock != self.target.clock:
            raise ValueError("SumInto output must use exactly the target clock")
        if self.clock.contract != self.target.clock.contract:
            raise ValueError("SumInto output must inherit the target ClockContract")
