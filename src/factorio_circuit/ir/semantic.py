"""Logical circuit IR: canonical clocked Level flows plus compatibility records.

The symbolic frontend still emits ``InputSample`` and ``VectorInputSample`` for compatibility.  The
frontend-to-IR boundary contextualizes them into the Flow-bearing records in this module before
ordinary optimization, timing, simulation, and physical lowering.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from enum import StrEnum

from factorio_circuit.events import EventCausalityError, EventCompilationError, EventCrossingError
from factorio_circuit.ir.physical import SignalId
from factorio_circuit.ir.state import (
    AccumulatorAdd,
    AccumulatorClear,
    EventStateOperation,
    FreezeSet,
    StateOperation,
    StateRegister,
    StateTransition,
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
class ClockContract:
    """Timing knowledge associated with a structural clock.

    Contracts are deliberately not part of a clock's structural identity.  Analysis may refine a
    contract without manufacturing a new logical clock.  ``guaranteed_min_separation`` remains the
    small compatibility surface used by the Event reference lane.
    """

    guaranteed_min_separation: int = 1

    def __post_init__(self) -> None:
        if (
            isinstance(self.guaranteed_min_separation, bool)
            or not isinstance(self.guaranteed_min_separation, int)
            or self.guaranteed_min_separation < 1
        ):
            raise ValueError("clock minimum separation must be a positive integer")


@dataclass(frozen=True, slots=True)
class ClockId:
    """Structural identity for a clock, independent of any timing contract."""

    identity: str
    provenance: ClockProvenance

    def __post_init__(self) -> None:
        if not isinstance(self.identity, str) or not self.identity:
            raise ValueError("clock identity must be non-empty")
        if not isinstance(self.provenance, ClockProvenance):
            raise ValueError("clock provenance must be a ClockProvenance")


@dataclass(frozen=True, slots=True, eq=False, init=False)
class Clock:
    """An immutable structural semantic clock identity.

    The third positional argument is retained as a compatibility spelling for the timing
    contract.  It is excluded from equality and hashing: two observations of the same activation
    stream remain the same clock even when timing analysis knows different bounds for them.
    """

    identity: str
    provenance: ClockProvenance
    contract: ClockContract

    def __init__(
        self,
        identity: str,
        provenance: ClockProvenance,
        guaranteed_min_separation: int = 1,
        *,
        contract: ClockContract | None = None,
    ) -> None:
        if not isinstance(identity, str) or not identity:
            raise ValueError("clock identity must be non-empty")
        if not isinstance(provenance, ClockProvenance):
            raise ValueError("clock provenance must be a ClockProvenance")
        if contract is None:
            contract = ClockContract(guaranteed_min_separation)
        elif not isinstance(contract, ClockContract):
            raise ValueError("clock contract must be a ClockContract")
        elif guaranteed_min_separation != 1 and (
            guaranteed_min_separation != contract.guaranteed_min_separation
        ):
            raise ValueError("clock contract and compatibility separation disagree")
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "contract", contract)

    @property
    def guaranteed_min_separation(self) -> int:
        """Compatibility view of :attr:`contract`."""

        return self.contract.guaranteed_min_separation

    @property
    def clock_id(self) -> ClockId:
        """Return the contract-free structural identity used by analysis."""

        return ClockId(self.identity, self.provenance)

    @property
    def id(self) -> ClockId:
        """Compatibility spelling for :attr:`clock_id`."""

        return self.clock_id

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Clock):
            return NotImplemented
        return self.identity == other.identity and self.provenance is other.provenance

    def __hash__(self) -> int:
        return hash((self.identity, self.provenance))


@dataclass(frozen=True, slots=True)
class ClockContractEnvironment:
    """Authoritative analysis-time contracts keyed by structural :class:`ClockId` values.

    ``Clock.contract`` is retained for compatibility at API boundaries.  Analysis uses this
    immutable environment instead, so refining a guarantee never changes a logical clock identity.
    """

    entries: tuple[tuple[ClockId, ClockContract], ...] = ()

    def __post_init__(self) -> None:
        ids = [clock_id for clock_id, _ in self.entries]
        if len(set(ids)) != len(ids):
            raise ValueError("clock contract environment contains duplicate clock identities")
        if any(not isinstance(clock_id, ClockId) for clock_id, _ in self.entries):
            raise ValueError("clock contract environment keys must be ClockId values")
        if any(not isinstance(contract, ClockContract) for _, contract in self.entries):
            raise ValueError("clock contract environment values must be ClockContract values")

    @classmethod
    def from_clocks(
        cls, clocks: tuple[Clock, ...] | list[Clock] | set[Clock]
    ) -> ClockContractEnvironment:
        contracts: dict[ClockId, ClockContract] = {}
        for clock in clocks:
            existing = contracts.get(clock.clock_id)
            if existing is not None and existing != clock.contract:
                raise ValueError(
                    f"conflicting ClockContract declarations for ClockId "
                    f"{clock.clock_id.identity!r}"
                )
            if existing is None:
                contracts[clock.clock_id] = clock.contract
        return cls(tuple(contracts.items()))

    @classmethod
    def from_module(cls, module: object) -> ClockContractEnvironment:
        clocks: list[Clock] = []
        seen: set[int] = set()

        def collect(value: object) -> None:
            if id(value) in seen:
                return
            seen.add(id(value))
            if isinstance(value, Clock):
                clocks.append(value)
                return
            if isinstance(value, Mapping):
                for key, item in value.items():
                    collect(key)
                    collect(item)
                return
            if isinstance(value, (tuple, list, set, frozenset)):
                for item in value:
                    collect(item)
                return
            if is_dataclass(value):
                for item in fields(value):
                    collect(getattr(value, item.name))

        collect(module)
        # ``state_transitions`` projects legacy state records when a module has no canonical tuple;
        # visiting the projection makes its clocks authoritative too.
        from factorio_circuit.ir.state import state_transitions

        for transition in state_transitions(module):
            collect(transition)
        return cls.from_clocks(clocks)

    def contract_for(self, clock: Clock | ClockId) -> ClockContract:
        clock_id = clock if isinstance(clock, ClockId) else clock.clock_id
        for candidate, contract in self.entries:
            if candidate == clock_id:
                return contract
        raise KeyError(clock_id)

    def with_minimum(self, clock: Clock | ClockId, minimum: int) -> ClockContractEnvironment:
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
            raise ValueError("clock minimum separation must be a positive integer")
        clock_id = clock if isinstance(clock, ClockId) else clock.clock_id
        current = self.contract_for(clock)
        contract = ClockContract(max(current.guaranteed_min_separation, minimum))
        return ClockContractEnvironment(
            tuple(
                (candidate, contract if candidate == clock_id else value)
                for candidate, value in self.entries
            )
            if any(candidate == clock_id for candidate, _ in self.entries)
            else (*self.entries, (clock_id, contract))
        )


@dataclass(frozen=True, slots=True)
class Flow:
    """Immutable metadata describing a clocked flow reference."""

    reference: object
    payload_shape: PayloadShape
    modality: TemporalModality
    clock: Clock
    logical_offset: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.payload_shape, PayloadShape):
            raise ValueError("flow payload_shape must be a PayloadShape")
        if not isinstance(self.modality, TemporalModality):
            raise ValueError("flow modality must be a TemporalModality")
        if not isinstance(self.clock, Clock):
            raise ValueError("flow clock must be a Clock")
        if isinstance(self.logical_offset, bool) or not isinstance(self.logical_offset, int):
            raise ValueError("flow logical_offset must be an integer")


class CanonicalInvariantError(ValueError):
    """Raised when a Level-only internal entry point receives non-canonical semantic IR."""


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
class EventScalarFlow:
    """Canonical scalar Event source on its occurrence clock."""

    source: EventInput
    flow: Flow

    def __post_init__(self) -> None:
        if self.source.payload_shape is not PayloadShape.SCALAR:
            raise EventCrossingError("scalar Event Flow requires a scalar Event source")
        if (
            self.flow.reference != self.source
            or self.flow.payload_shape is not PayloadShape.SCALAR
            or self.flow.modality is not TemporalModality.EVENT
            or self.flow.clock != self.source.clock
            or self.flow.logical_offset != 0
        ):
            raise EventCrossingError("scalar Event Flow metadata is inconsistent with its source")

    @property
    def name(self) -> str:
        return self.source.name


@dataclass(frozen=True, slots=True)
class EventVectorFlow:
    """Canonical vector Event source on its occurrence clock."""

    source: EventInput
    flow: Flow

    def __post_init__(self) -> None:
        if self.source.payload_shape is not PayloadShape.VECTOR:
            raise EventCrossingError("vector Event Flow requires a vector Event source")
        if (
            self.flow.reference != self.source
            or self.flow.payload_shape is not PayloadShape.VECTOR
            or self.flow.modality is not TemporalModality.EVENT
            or self.flow.clock != self.source.clock
            or self.flow.logical_offset != 0
        ):
            raise EventCrossingError("vector Event Flow metadata is inconsistent with its source")

    @property
    def name(self) -> str:
        return self.source.name


FlowEventInput = EventScalarFlow
FlowEventVectorInput = EventVectorFlow


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
class FlowInput(Input):
    """Canonical scalar Level source contextualized to one consumer clock.

    ``source`` points at the compatibility ``Input`` used by backend input markers.  Keeping that
    identity separate lets the canonical value carry Flow metadata without changing the public
    legacy input dataclass.
    """

    source: Input = field(compare=False, repr=False)
    flow: Flow


@dataclass(frozen=True, slots=True)
class FlowInputSample(InputSample):
    """Canonical scalar Level observation with preserved logical offset."""

    flow: Flow | None = None


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
class FlowVectorInput(VectorInput):
    """Canonical whole-vector Level source contextualized to one consumer clock."""

    source: VectorInput = field(compare=False, repr=False)
    flow: Flow


@dataclass(frozen=True, slots=True)
class FlowVectorInputSample(VectorInputSample):
    """Canonical whole-vector Level observation with preserved logical offset."""

    flow: Flow | None = None


@dataclass(frozen=True, slots=True)
class SampleOn:
    """A semantic-only Level observation taken when an Event target occurs."""

    source: object
    target: EventInput
    flow: Flow | None = None

    def __post_init__(self) -> None:
        if not isinstance(
            self.source, (Input, VectorInput, InputSample, VectorInputSample, VectorConstant)
        ) and not (
            isinstance(
                self.source,
                (
                    BinaryOp,
                    Compare,
                    Select,
                    VectorSignal,
                    VectorBinaryOp,
                    VectorScalarOp,
                    VectorFilter,
                    VectorSelect,
                    VectorRegisterRead,
                ),
            )
        ):
            raise EventCrossingError("SampleOn source must be a Level expression")
        if not isinstance(self.target, EventInput):
            raise EventCrossingError("SampleOn target must be a declared Event input")
        if self.flow is None:
            shape = PayloadShape.VECTOR if is_vector_value(self.source) else PayloadShape.SCALAR
            object.__setattr__(
                self,
                "flow",
                Flow(self.source, shape, TemporalModality.EVENT, self.target.clock),
            )
        if self.flow is not None:
            shape = PayloadShape.VECTOR if is_vector_value(self.source) else PayloadShape.SCALAR
            if (
                self.flow.reference != self.source
                or self.flow.payload_shape is not shape
                or self.flow.modality is not TemporalModality.EVENT
                or self.flow.clock != self.target.clock
                or self.flow.logical_offset != 0
            ):
                raise EventCrossingError("SampleOn Flow metadata is inconsistent with its crossing")


@dataclass(frozen=True, slots=True)
class VectorConstant:
    """A constant whole-vector stream."""

    signals: tuple[tuple[SignalId, int], ...]
    name: str | None = None
    flow: Flow | None = None


@dataclass(frozen=True, slots=True)
class Constant:
    value: int
    name: str | None = None
    flow: Flow | None = None


@dataclass(frozen=True, slots=True)
class BinaryOp:
    op: str
    left: ScalarValue
    right: ScalarValue
    name: str | None = None
    flow: Flow | None = None

    def __post_init__(self) -> None:
        ensure_expression_flow(self, PayloadShape.SCALAR)


@dataclass(frozen=True, slots=True)
class Compare:
    op: str
    left: ScalarValue
    right: ScalarValue
    name: str | None = None
    flow: Flow | None = None

    def __post_init__(self) -> None:
        ensure_expression_flow(self, PayloadShape.SCALAR)


@dataclass(frozen=True, slots=True)
class Select:
    """Select ``when_true`` when ``condition != 0``, otherwise ``when_false``."""

    condition: ScalarValue
    when_true: ScalarValue
    when_false: ScalarValue
    name: str | None = None
    flow: Flow | None = None

    def __post_init__(self) -> None:
        ensure_expression_flow(self, PayloadShape.SCALAR)


@dataclass(frozen=True, slots=True)
class VectorSignal:
    """Read one concrete signal lane from a whole-vector stream."""

    vector: VectorValue
    signal: SignalId
    name: str | None = None
    flow: Flow | None = None

    def __post_init__(self) -> None:
        ensure_expression_flow(self, PayloadShape.SCALAR)


@dataclass(frozen=True, slots=True)
class VectorBinaryOp:
    """Canonical public whole-vector lane-wise binary operation."""

    op: str
    left: VectorValue
    right: VectorValue
    flow: Flow | None = None

    def __post_init__(self) -> None:
        ensure_expression_flow(self, PayloadShape.VECTOR)


@dataclass(frozen=True, slots=True)
class VectorScalarOp:
    """Canonical public whole-vector operation with a scalar operand."""

    op: str
    vector: VectorValue
    scalar: ScalarValue
    flow: Flow | None = None

    def __post_init__(self) -> None:
        ensure_expression_flow(self, PayloadShape.VECTOR)


@dataclass(frozen=True, slots=True)
class VectorFilter:
    """Canonical public whole-vector lane filter."""

    op: str
    vector: VectorValue
    right: int
    flow: Flow | None = None

    def __post_init__(self) -> None:
        ensure_expression_flow(self, PayloadShape.VECTOR)


@dataclass(frozen=True, slots=True, init=False)
class VectorSelect(VectorFilter):
    """Canonical public whole-vector selector."""

    select_max: bool = True
    index: int = 0

    def __init__(
        self,
        op: str,
        vector: VectorValue,
        right: int,
        select_max: bool = True,
        index: int = 0,
        *,
        flow: Flow | None = None,
    ) -> None:
        object.__setattr__(self, "op", op)
        object.__setattr__(self, "vector", vector)
        object.__setattr__(self, "right", right)
        object.__setattr__(self, "flow", flow)
        object.__setattr__(self, "select_max", select_max)
        object.__setattr__(self, "index", index)
        self.__post_init__()


@dataclass(frozen=True, slots=True)
class FlowVectorRegisterRead(VectorRegisterRead):
    """Canonical state-vector observation with Flow metadata."""

    flow: Flow | None = field(default=None, kw_only=True)


LegacyScalarValue = Input | InputSample | Constant | BinaryOp | Compare | Select | VectorSignal
LegacyVectorValue = (
    VectorInput
    | VectorInputSample
    | VectorConstant
    | VectorRegisterRead
    | VectorBinaryOp
    | VectorScalarOp
    | VectorFilter
    | VectorSelect
)
ScalarValue = LegacyScalarValue | EventScalarFlow | SampleOn
VectorValue = LegacyVectorValue | EventVectorFlow | SampleOn
Value = ScalarValue
OutputValue = ScalarValue | VectorValue
DerivedValue = BinaryOp | Compare | Select
CanonicalScalarValue = (
    FlowInput
    | FlowInputSample
    | EventScalarFlow
    | Constant
    | BinaryOp
    | Compare
    | Select
    | VectorSignal
    | SampleOn
)
CanonicalVectorValue = (
    FlowVectorInput
    | FlowVectorInputSample
    | VectorConstant
    | FlowVectorRegisterRead
    | VectorBinaryOp
    | VectorScalarOp
    | VectorFilter
    | VectorSelect
    | EventVectorFlow
    | SampleOn
)
CanonicalValue = CanonicalScalarValue | CanonicalVectorValue


def is_vector_value(value: object) -> bool:
    """Return whether ``value`` is a whole-vector node without relying on subclass coincidence."""

    return isinstance(
        value,
        (
            VectorInput,
            VectorInputSample,
            VectorConstant,
            VectorRegisterRead,
            VectorBinaryOp,
            VectorScalarOp,
            VectorFilter,
            VectorSelect,
            FlowVectorInput,
            FlowVectorInputSample,
            FlowVectorRegisterRead,
            EventVectorFlow,
        ),
    ) or (isinstance(value, SampleOn) and is_vector_value(value.source))


@dataclass(frozen=True, slots=True)
class FlowFacts:
    """Recursive modality/clock facts for one semantic expression."""

    shape: PayloadShape
    modality: TemporalModality | None
    clock: Clock | None


def _merge_flow_facts(
    shape: PayloadShape,
    facts: tuple[FlowFacts, ...],
    own: Flow | None,
) -> FlowFacts:
    modalities = {fact.modality for fact in facts if fact.modality is not None}
    clocks = {fact.clock for fact in facts if fact.clock is not None}
    if own is not None:
        if own.payload_shape is not shape:
            raise EventCrossingError("Flow payload shape does not match its expression")
        modalities.add(own.modality)
        clocks.add(own.clock)
    if len(modalities) > 1:
        raise EventCrossingError(
            "Event and Level expressions require an explicit SampleOn conversion"
        )
    modality = next(iter(modalities), None)
    if modality is TemporalModality.EVENT and len(clocks) > 1:
        raise EventCausalityError("Event expressions must use one compatible occurrence clock")
    return FlowFacts(shape, modality, next(iter(clocks), None))


def _flow_facts(value: object, expected: PayloadShape) -> FlowFacts:
    if isinstance(value, EventScalarFlow):
        return _merge_flow_facts(expected, (), value.flow)
    if isinstance(value, EventVectorFlow):
        return _merge_flow_facts(expected, (), value.flow)
    if isinstance(value, SampleOn):
        if (
            PayloadShape.VECTOR if is_vector_value(value.source) else PayloadShape.SCALAR
        ) is not expected:
            raise EventCrossingError("SampleOn expression shape does not match its consumer")
        return _merge_flow_facts(expected, (), value.flow)
    if isinstance(
        value,
        (
            FlowInput,
            FlowInputSample,
            FlowVectorInput,
            FlowVectorInputSample,
            FlowVectorRegisterRead,
        ),
    ):
        return _merge_flow_facts(expected, (), value.flow)
    if isinstance(value, (Input, InputSample, VectorInput, VectorInputSample, VectorRegisterRead)):
        return FlowFacts(expected, TemporalModality.LEVEL, None)
    if isinstance(value, (Constant, VectorConstant)):
        return _merge_flow_facts(expected, (), getattr(value, "flow", None))
    if isinstance(value, (BinaryOp, Compare)):
        return _merge_flow_facts(
            expected,
            (
                _flow_facts(value.left, PayloadShape.SCALAR),
                _flow_facts(value.right, PayloadShape.SCALAR),
            ),
            value.flow,
        )
    if isinstance(value, Select):
        return _merge_flow_facts(
            expected,
            tuple(
                _flow_facts(child, PayloadShape.SCALAR)
                for child in (value.condition, value.when_true, value.when_false)
            ),
            value.flow,
        )
    if isinstance(value, VectorSignal):
        return _merge_flow_facts(
            expected,
            (_flow_facts(value.vector, PayloadShape.VECTOR),),
            value.flow,
        )
    if isinstance(value, VectorBinaryOp):
        return _merge_flow_facts(
            expected,
            (
                _flow_facts(value.left, PayloadShape.VECTOR),
                _flow_facts(value.right, PayloadShape.VECTOR),
            ),
            value.flow,
        )
    if isinstance(value, VectorScalarOp):
        return _merge_flow_facts(
            expected,
            (
                _flow_facts(value.vector, PayloadShape.VECTOR),
                _flow_facts(value.scalar, PayloadShape.SCALAR),
            ),
            value.flow,
        )
    if isinstance(value, (VectorFilter, VectorSelect)):
        return _merge_flow_facts(
            expected,
            (_flow_facts(value.vector, PayloadShape.VECTOR),),
            value.flow,
        )
    raise EventCrossingError(f"unsupported {expected.value} expression {type(value).__name__}")


def infer_expression_flow(value: object, expected: PayloadShape | None = None) -> Flow | None:
    """Infer a recursive Flow while preserving neutral constants and raw Level clocks."""

    shape = expected or (PayloadShape.VECTOR if is_vector_value(value) else PayloadShape.SCALAR)
    facts = _flow_facts(value, shape)
    if facts.modality is None or facts.clock is None:
        return getattr(value, "flow", None)
    existing = getattr(value, "flow", None)
    if isinstance(existing, Flow) and (
        existing.payload_shape is facts.shape
        and existing.modality is facts.modality
        and existing.clock == facts.clock
    ):
        return existing
    return Flow(value, facts.shape, facts.modality, facts.clock)


def validate_expression_flow(value: object, expected: PayloadShape | None = None) -> FlowFacts:
    """Validate recursive modality, clock, shape, and explicit SampleOn boundaries."""

    shape = expected or (PayloadShape.VECTOR if is_vector_value(value) else PayloadShape.SCALAR)
    return _flow_facts(value, shape)


def ensure_expression_flow(value: object, expected: PayloadShape | None = None) -> FlowFacts:
    """Validate an expression and attach its inferred non-neutral Flow metadata in place."""

    facts = validate_expression_flow(value, expected)
    flow = infer_expression_flow(value, expected)
    if flow is not None and getattr(value, "flow", None) is None:
        object.__setattr__(
            value,
            "flow",
            Flow(None, flow.payload_shape, flow.modality, flow.clock, flow.logical_offset),
        )
    return facts


def is_vector_expression(value: object) -> bool:
    """Return whether ``value`` is a derived vector expression requiring vector lowering."""

    return isinstance(value, (VectorBinaryOp, VectorScalarOp, VectorFilter, VectorSelect))


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
    register_clocks: tuple[tuple[StateRegister, Clock], ...] = ()
    transitions: tuple[StateTransition, ...] = ()

    def __post_init__(self) -> None:
        from factorio_circuit.ir.state import state_transitions

        state_transitions(self)
        for value in self.output.values:
            validate_expression_flow(value)
        for semantic_operation in self.operations:
            validate_expression_flow(semantic_operation, PayloadShape.SCALAR)
        for state_operation in self.state_operations:
            if isinstance(state_operation, (AccumulatorAdd, FreezeSet)):
                validate_expression_flow(state_operation.value, PayloadShape.VECTOR)
            if isinstance(state_operation, (AccumulatorAdd, AccumulatorClear, FreezeSet)):
                validate_expression_flow(state_operation.when, PayloadShape.SCALAR)
        for event_operation in self.event_state_operations:
            if event_operation.value is not None:
                validate_expression_flow(event_operation.value, PayloadShape.VECTOR)
        for transition in self.transitions:
            if transition.value is not None:
                validate_expression_flow(transition.value, PayloadShape.VECTOR)
            if transition.when is not None:
                validate_expression_flow(transition.when, PayloadShape.SCALAR)


def _contains_event_value(value: object, seen: set[int] | None = None) -> bool:
    if seen is None:
        seen = set()
    if id(value) in seen:
        return False
    seen.add(id(value))
    if isinstance(value, (EventScalarFlow, EventVectorFlow, SampleOn)):
        return True
    if isinstance(value, (BinaryOp, Compare)):
        return _contains_event_value(value.left, seen) or _contains_event_value(value.right, seen)
    if isinstance(value, Select):
        return any(
            _contains_event_value(child, seen)
            for child in (value.condition, value.when_true, value.when_false)
        )
    if isinstance(value, VectorSignal):
        return _contains_event_value(value.vector, seen)
    if isinstance(value, VectorBinaryOp):
        return _contains_event_value(value.left, seen) or _contains_event_value(value.right, seen)
    if isinstance(value, VectorScalarOp):
        return _contains_event_value(value.vector, seen) or _contains_event_value(
            value.scalar, seen
        )
    if isinstance(value, (VectorFilter, VectorSelect)):
        return _contains_event_value(value.vector, seen)
    return False


def has_event_usage(module: CircuitModule) -> bool:
    """Return whether Event values/transitions are used beyond a bare declaration."""

    from factorio_circuit.ir.state import FreezeCapture

    return (
        bool(module.event_state_operations)
        or bool(module.sample_on_crossings)
        or any(_contains_event_value(value) for value in module.output.values)
        or any(_contains_event_value(operation) for operation in module.operations)
        or any(
            _contains_event_value(getattr(operation, field_name, None))
            for operation in module.state_operations
            for field_name in ("value", "when")
        )
        or any(isinstance(operation, FreezeCapture) for operation in module.state_operations)
        or any(transition.kind == "capture" for transition in module.transitions)
        or any(
            transition.clock.provenance is ClockProvenance.EXTERNAL_EVENT
            for transition in module.transitions
        )
    )


def contains_event_semantics(module: CircuitModule) -> bool:
    """Return whether ``module`` requires the semantic-only Event reference path."""

    return bool(module.event_inputs) or has_event_usage(module)


def _canonical_flow(value: object, shape: PayloadShape) -> Flow:
    flow = getattr(value, "flow", None)
    if not isinstance(flow, Flow):
        raise CanonicalInvariantError(
            f"canonical {shape.value} value {type(value).__name__} is missing Flow metadata"
        )
    if flow.payload_shape is not shape or flow.modality is not TemporalModality.LEVEL:
        raise CanonicalInvariantError(
            f"canonical {type(value).__name__} must carry a LEVEL {shape.value} Flow"
        )
    return flow


def _canonical_clock_map(module: CircuitModule) -> dict[StateRegister, Clock]:
    if len(module.register_clocks) != len(module.state_registers):
        raise CanonicalInvariantError(
            "canonical module must carry exactly one structural clock for every state register"
        )
    mapping: dict[StateRegister, Clock] = {}
    for register, clock in module.register_clocks:
        if register not in module.state_registers:
            raise CanonicalInvariantError(
                f"canonical clock mapping references undeclared state {register.name!r}"
            )
        if register in mapping:
            raise CanonicalInvariantError(f"duplicate canonical clock for state {register.name!r}")
        if not isinstance(clock, Clock):
            raise CanonicalInvariantError(
                "canonical state clock mapping contains a non-Clock value"
            )
        mapping[register] = clock
    if set(mapping) != set(module.state_registers):
        raise CanonicalInvariantError("canonical state clock mapping is incomplete")
    return mapping


def _validate_canonical_scalar(
    value: object,
    register_clocks: dict[StateRegister, Clock],
    expected: Clock | None = None,
) -> Flow:
    if type(value) in (Input, InputSample):
        raise CanonicalInvariantError(
            f"raw legacy scalar {type(value).__name__} crossed the canonical boundary"
        )
    if isinstance(value, FlowInput):
        flow = _canonical_flow(value, PayloadShape.SCALAR)
        if not isinstance(value.source, Input) or flow.logical_offset != 0:
            raise CanonicalInvariantError("canonical scalar source wrapper is malformed")
    elif isinstance(value, FlowInputSample):
        flow = _canonical_flow(value, PayloadShape.SCALAR)
        if not isinstance(value.source, Input) or flow.logical_offset != value.offset:
            raise CanonicalInvariantError("canonical scalar sample offset is inconsistent")
    elif isinstance(value, Constant):
        flow = _canonical_flow(value, PayloadShape.SCALAR)
    elif isinstance(value, (BinaryOp, Compare)):
        flow = _canonical_flow(value, PayloadShape.SCALAR)
        _validate_canonical_scalar(value.left, register_clocks, flow.clock)
        _validate_canonical_scalar(value.right, register_clocks, flow.clock)
    elif isinstance(value, Select):
        flow = _canonical_flow(value, PayloadShape.SCALAR)
        for child in (value.condition, value.when_true, value.when_false):
            _validate_canonical_scalar(child, register_clocks, flow.clock)
    elif isinstance(value, VectorSignal):
        flow = _canonical_flow(value, PayloadShape.SCALAR)
        _validate_canonical_vector(value.vector, register_clocks, flow.clock)
    else:
        raise CanonicalInvariantError(
            f"unsupported or non-canonical scalar value {type(value).__name__}"
        )
    if expected is not None and flow.clock != expected:
        raise CanonicalInvariantError(
            f"canonical scalar clock mismatch: {flow.clock.identity!r} != {expected.identity!r}"
        )
    return flow


def _validate_canonical_vector(
    value: object,
    register_clocks: dict[StateRegister, Clock],
    expected: Clock | None = None,
) -> Flow:
    if type(value) in (VectorInput, VectorInputSample, VectorRegisterRead):
        raise CanonicalInvariantError(
            f"raw legacy vector {type(value).__name__} crossed the canonical boundary"
        )
    if isinstance(value, FlowVectorInput):
        flow = _canonical_flow(value, PayloadShape.VECTOR)
        if not isinstance(value.source, VectorInput) or flow.logical_offset != 0:
            raise CanonicalInvariantError("canonical vector source wrapper is malformed")
    elif isinstance(value, FlowVectorInputSample):
        flow = _canonical_flow(value, PayloadShape.VECTOR)
        if not isinstance(value.source, VectorInput) or flow.logical_offset != value.offset:
            raise CanonicalInvariantError("canonical vector sample offset is inconsistent")
    elif isinstance(value, VectorConstant):
        flow = _canonical_flow(value, PayloadShape.VECTOR)
    elif isinstance(value, FlowVectorRegisterRead):
        flow = _canonical_flow(value, PayloadShape.VECTOR)
        if flow.logical_offset != value.offset or register_clocks.get(value.register) != flow.clock:
            raise CanonicalInvariantError("canonical state-vector clock or offset is inconsistent")
    elif isinstance(value, VectorBinaryOp):
        flow = _canonical_flow(value, PayloadShape.VECTOR)
        _validate_canonical_vector(value.left, register_clocks, flow.clock)
        _validate_canonical_vector(value.right, register_clocks, flow.clock)
    elif isinstance(value, VectorScalarOp):
        flow = _canonical_flow(value, PayloadShape.VECTOR)
        _validate_canonical_vector(value.vector, register_clocks, flow.clock)
        _validate_canonical_scalar(value.scalar, register_clocks, flow.clock)
    elif isinstance(value, (VectorSelect, VectorFilter)):
        flow = _canonical_flow(value, PayloadShape.VECTOR)
        _validate_canonical_vector(value.vector, register_clocks, flow.clock)
    else:
        raise CanonicalInvariantError(
            f"unsupported or non-canonical vector value {type(value).__name__}"
        )
    if expected is not None and flow.clock != expected:
        raise CanonicalInvariantError(
            f"canonical vector clock mismatch: {flow.clock.identity!r} != {expected.identity!r}"
        )
    return flow


def validate_canonical_module(module: CircuitModule) -> None:
    """Validate the invariant required by Level timing, simulation, and lowering internals."""

    if contains_event_semantics(module):
        raise CanonicalInvariantError("Event modules do not enter the Level canonical boundary")
    register_clocks = _canonical_clock_map(module)
    for value in module.output.values:
        if isinstance(
            value,
            (
                VectorInput,
                VectorInputSample,
                VectorConstant,
                VectorRegisterRead,
                VectorBinaryOp,
                VectorScalarOp,
                VectorFilter,
                VectorSelect,
            ),
        ):
            _validate_canonical_vector(value, register_clocks)
        else:
            _validate_canonical_scalar(value, register_clocks)
    for operation in module.operations:
        _validate_canonical_scalar(operation, register_clocks)
    for state_operation in module.state_operations:
        try:
            expected = register_clocks[state_operation.register]
        except KeyError as exc:
            raise CanonicalInvariantError(
                "state operation has no canonical register clock"
            ) from exc
        if isinstance(state_operation, (AccumulatorAdd, FreezeSet)):
            _validate_canonical_vector(state_operation.value, register_clocks, expected)
        if isinstance(state_operation, (AccumulatorAdd, AccumulatorClear, FreezeSet)):
            _validate_canonical_scalar(state_operation.when, register_clocks, expected)
    for transition in module.transitions:
        try:
            expected = register_clocks[transition.register]
        except KeyError as exc:
            raise CanonicalInvariantError(
                "state transition has no canonical register clock"
            ) from exc
        if transition.clock != expected:
            raise CanonicalInvariantError(
                "state transition clock does not match its register clock"
            )
        if transition.kind in {"add", "set"}:
            if transition.value is None:
                raise CanonicalInvariantError(
                    f"state transition {transition.kind!r} requires a vector value"
                )
            _validate_canonical_vector(transition.value, register_clocks, expected)
        if transition.kind in {"add", "clear", "set"}:
            if transition.when is None:
                raise CanonicalInvariantError(
                    f"state transition {transition.kind!r} requires a scalar condition"
                )
            _validate_canonical_scalar(transition.when, register_clocks, expected)


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
