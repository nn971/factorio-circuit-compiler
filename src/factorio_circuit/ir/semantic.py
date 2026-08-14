"""Logical circuit IR: sampled scalar dataflow plus whole-vector state boundaries.

The clocked-flow records in this module are deliberately additive.  The compiler still consumes
``InputSample`` and ``VectorInputSample`` as its semantic source nodes; the records below provide
the vocabulary needed to describe those sources without changing that downstream representation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from factorio_circuit.events import EventCompilationError, EventCrossingError
from factorio_circuit.ir.physical import SignalId
from factorio_circuit.ir.state import (
    EventStateOperation,
    StateOperation,
    StateRegister,
    VectorRegisterRead,
)


class PayloadShape(StrEnum):
    """The shape of a clocked-flow payload."""

    SCALAR = "scalar"
    VECTOR = "vector"


class TemporalModality(StrEnum):
    """How a payload behaves between clock occurrences."""

    LEVEL = "level"
    EVENT = "event"


class ClockProvenance(StrEnum):
    """The origin of a semantic clock identity."""

    INFERRED = "inferred"
    FIXED_PERIODIC = "fixed_periodic"
    EXTERNAL_EVENT = "external_event"
    DERIVED = "derived"


@dataclass(frozen=True, slots=True)
class Clock:
    """An immutable semantic clock identity and its conservative timing guarantee."""

    identity: str
    provenance: ClockProvenance
    guaranteed_min_separation: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.identity, str) or not self.identity:
            raise ValueError("clock identity must be non-empty")
        if not isinstance(self.provenance, ClockProvenance):
            raise ValueError("clock provenance must be a ClockProvenance")
        if (
            isinstance(self.guaranteed_min_separation, bool)
            or not isinstance(self.guaranteed_min_separation, int)
            or self.guaranteed_min_separation < 1
        ):
            raise ValueError("clock minimum separation must be a positive integer")


@dataclass(frozen=True, slots=True)
class Flow:
    """Immutable metadata describing a clocked flow reference."""

    reference: object
    payload_shape: PayloadShape
    modality: TemporalModality
    clock: Clock
    logical_offset: int = 0


@dataclass(frozen=True, slots=True)
class EventInput:
    """A declared external Event source; executable behavior lives in reference simulation."""

    name: str
    payload_shape: PayloadShape
    clock: Clock

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("event input name must be non-empty")
        if not isinstance(self.payload_shape, PayloadShape):
            raise ValueError("event input payload_shape must be a PayloadShape")
        if not isinstance(self.clock, Clock):
            raise ValueError("event input clock must be a Clock")
        if self.clock.provenance is not ClockProvenance.EXTERNAL_EVENT:
            raise ValueError("event input clock must have EXTERNAL_EVENT provenance")


@dataclass(frozen=True, slots=True)
class Input:
    """A scalar external source observed at logical step zero by default."""

    name: str


@dataclass(frozen=True, slots=True)
class InputSample:
    """Observation of ``source`` at logical offset ``offset``."""

    source: Input
    offset: int
    name: str | None = None


@dataclass(frozen=True, slots=True)
class VectorInput:
    """A complete Factorio signal-map source at logical step zero by default."""

    name: str


@dataclass(frozen=True, slots=True)
class VectorInputSample:
    """Whole-vector observation of ``source`` at logical offset ``offset``."""

    source: VectorInput
    offset: int
    name: str | None = None


@dataclass(frozen=True, slots=True)
class SampleOn:
    """A semantic-only Level observation taken when an Event target occurs."""

    source: Input | VectorInput
    target: EventInput

    def __post_init__(self) -> None:
        if not isinstance(self.source, (Input, VectorInput)):
            raise EventCrossingError("SampleOn source must be a raw Level input")
        if not isinstance(self.target, EventInput):
            raise EventCrossingError("SampleOn target must be a declared Event input")


@dataclass(frozen=True, slots=True)
class VectorConstant:
    """A constant whole-vector stream."""

    signals: tuple[tuple[SignalId, int], ...]
    name: str | None = None


@dataclass(frozen=True, slots=True)
class Constant:
    value: int
    name: str | None = None


@dataclass(frozen=True, slots=True)
class BinaryOp:
    op: str
    left: ScalarValue
    right: ScalarValue
    name: str | None = None


@dataclass(frozen=True, slots=True)
class Compare:
    op: str
    left: ScalarValue
    right: ScalarValue
    name: str | None = None


@dataclass(frozen=True, slots=True)
class Select:
    """Select ``when_true`` when ``condition != 0``, otherwise ``when_false``."""

    condition: ScalarValue
    when_true: ScalarValue
    when_false: ScalarValue
    name: str | None = None


@dataclass(frozen=True, slots=True)
class VectorSignal:
    """Read one concrete signal lane from a whole-vector stream."""

    vector: VectorValue
    signal: SignalId
    name: str | None = None


ScalarValue = Input | InputSample | Constant | BinaryOp | Compare | Select | VectorSignal
VectorValue = VectorInput | VectorInputSample | VectorConstant | VectorRegisterRead
Value = ScalarValue
OutputValue = ScalarValue | VectorValue
DerivedValue = BinaryOp | Compare | Select


@dataclass(frozen=True, slots=True)
class ReturnValue:
    values: tuple[OutputValue, ...]
    names: tuple[str | None, ...] = ()

    def __post_init__(self) -> None:
        if self.names and len(self.names) != len(self.values):
            raise ValueError("output names must match output values")


@dataclass(frozen=True, slots=True)
class CircuitModule:
    name: str
    inputs: tuple[Input, ...]
    operations: tuple[DerivedValue, ...]
    output: ReturnValue
    vector_inputs: tuple[VectorInput, ...] = ()
    state_registers: tuple[StateRegister, ...] = ()
    state_operations: tuple[StateOperation, ...] = ()
    event_inputs: tuple[EventInput, ...] = ()
    event_state_operations: tuple[EventStateOperation, ...] = ()
    sample_on_crossings: tuple[SampleOn, ...] = ()


def contains_event_semantics(module: CircuitModule) -> bool:
    """Return whether ``module`` requires the semantic-only Event reference path."""

    from factorio_circuit.ir.state import FreezeCapture

    return (
        bool(module.event_inputs)
        or bool(module.event_state_operations)
        or bool(module.sample_on_crossings)
        or any(isinstance(operation, FreezeCapture) for operation in module.state_operations)
    )


def reject_event_module(module: CircuitModule) -> None:
    """Reject Event modules at Level/physical-only compiler boundaries."""

    if contains_event_semantics(module):
        raise EventCompilationError(
            "Event modules are semantic/reference-only and cannot use this Level or physical "
            "route; use simulate_events() for reference simulation"
        )


def dependencies(value: ScalarValue) -> tuple[ScalarValue, ...]:
    if isinstance(value, (Input, InputSample, Constant, VectorSignal)):
        return ()
    if isinstance(value, (BinaryOp, Compare)):
        return (value.left, value.right)
    if isinstance(value, Select):
        return (value.condition, value.when_true, value.when_false)
    raise TypeError(value)


def reachable_operations(module: CircuitModule) -> tuple[DerivedValue, ...]:
    """Return scalar stateless operations reachable from scalar outputs/state controls."""

    result: list[DerivedValue] = []
    seen: set[int] = set()

    def visit(value: ScalarValue) -> None:
        key = id(value)
        if key in seen:
            return
        seen.add(key)
        for child in dependencies(value):
            visit(child)
        if isinstance(value, (BinaryOp, Compare, Select)):
            result.append(value)

    for output in module.output.values:
        if isinstance(
            output, (Input, InputSample, Constant, BinaryOp, Compare, Select, VectorSignal)
        ):
            visit(output)
    from factorio_circuit.ir.state import AccumulatorAdd, AccumulatorClear, FreezeSet

    for op in module.state_operations:
        if isinstance(op, (AccumulatorAdd, AccumulatorClear, FreezeSet)):
            visit(op.when)
    return tuple(result)
