"""Logical whole-vector state primitives.

State accesses carry elaboration order and logical-step metadata. Physical Factorio phases and clock
periods are inferred later and are intentionally absent from this IR.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from factorio_circuit.ir.semantic import Clock, EventInput, ScalarValue, VectorValue


@dataclass(frozen=True, slots=True)
class AccumulatorRegister:
    """Whole-vector accumulator: memory += input; clear resets it."""

    name: str


@dataclass(frozen=True, slots=True)
class FreezeRegister:
    """Whole-vector sample/hold register.

    ``set`` high makes the register transparent at a logical update boundary; ``set`` low holds the
    previous logical state.
    """

    name: str


StateRegister = AccumulatorRegister | FreezeRegister


@dataclass(frozen=True, slots=True)
class VectorRegisterRead:
    """Observation of a whole-vector register at one logical step."""

    register: StateRegister
    offset: int = 0
    order: int = 0
    name: str | None = None


@dataclass(frozen=True, slots=True)
class AccumulatorAdd:
    register: AccumulatorRegister
    value: VectorValue
    when: ScalarValue
    order: int = 0


@dataclass(frozen=True, slots=True)
class AccumulatorClear:
    register: AccumulatorRegister
    when: ScalarValue
    order: int = 0


@dataclass(frozen=True, slots=True)
class FreezeSet:
    register: FreezeRegister
    value: VectorValue
    when: ScalarValue
    order: int = 0


@dataclass(frozen=True, slots=True)
class FreezeCapture:
    """Semantic-only capture of a vector value on an external Event occurrence."""

    register: FreezeRegister
    trigger: EventInput
    value: VectorValue | None
    required_min_separation: int
    order: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.required_min_separation, bool) or not isinstance(
            self.required_min_separation, int
        ):
            raise ValueError("capture minimum separation must be an integer")
        if self.required_min_separation < 1:
            raise ValueError("capture minimum separation must be positive")


StateOperation = AccumulatorAdd | AccumulatorClear | FreezeSet
EventStateOperation = FreezeCapture


@dataclass(frozen=True, slots=True)
class StateTransition:
    """Canonical state update shared by periodic and reference-only Event reactions.

    Legacy ``Accumulator*``/``FreezeSet``/``FreezeCapture`` records remain syntax and compatibility
    adapters.  ``kind`` is one of ``add``, ``clear``, ``set``, or ``capture``; Event transitions
    carry ``trigger`` and the source contract while periodic transitions carry the selected
    structural clock directly.
    """

    register: StateRegister
    kind: str
    clock: Clock
    order: int = 0
    value: VectorValue | None = None
    when: ScalarValue | None = None
    trigger: EventInput | None = None
    required_min_separation: int | None = None
    logical_offset: int = 0
    legacy: object | None = None

    def __post_init__(self) -> None:
        from factorio_circuit.ir.semantic import Clock, ClockProvenance, EventInput

        if not isinstance(self.clock, Clock):
            raise ValueError("state transition clock must be a Clock")
        if self.kind not in {"add", "clear", "set", "capture"}:
            raise ValueError(f"unsupported canonical state transition kind {self.kind!r}")
        if isinstance(self.order, bool) or not isinstance(self.order, int):
            raise ValueError("state transition order must be an integer")
        if isinstance(self.logical_offset, bool) or not isinstance(self.logical_offset, int):
            raise ValueError("state transition logical offset must be an integer")
        if self.trigger is not None:
            if not isinstance(self.trigger, EventInput):
                raise ValueError("Event state transition requires an Event trigger")
            if self.clock != self.trigger.clock:
                raise ValueError("Event state transition clock must match its trigger clock")
        elif self.clock.provenance is ClockProvenance.EXTERNAL_EVENT:
            raise ValueError("periodic state transition cannot use an Event clock")
        if self.kind == "capture":
            if self.trigger is None:
                raise ValueError("capture transition requires an Event trigger")
            if self.required_min_separation is not None and (
                isinstance(self.required_min_separation, bool)
                or not isinstance(self.required_min_separation, int)
                or self.required_min_separation < 1
            ):
                raise ValueError("capture transition minimum separation must be positive")
        else:
            if self.kind in {"add", "set"} and self.value is None:
                raise ValueError(f"{self.kind} transition requires a vector value")
            if self.when is None:
                raise ValueError(f"{self.kind} transition requires a scalar condition")


def state_transitions(module: object) -> tuple[StateTransition, ...]:
    """Return authoritative transitions and reject conflicting compatibility duplicates."""

    transitions = tuple(getattr(module, "transitions", ()))
    if any(not isinstance(item, StateTransition) for item in transitions):
        raise ValueError("module transitions must contain only StateTransition values")
    canonical = cast(tuple[StateTransition, ...], transitions)
    legacy = _legacy_state_transitions(module)
    if canonical and legacy:
        canonical_signatures = {_transition_signature(item) for item in canonical}
        missing = [
            item for item in legacy if _transition_signature(item) not in canonical_signatures
        ]
        if missing:
            raise ValueError(
                "module contains conflicting canonical and legacy state transition representations"
            )
        return canonical
    return canonical or legacy


def _transition_signature(transition: StateTransition) -> tuple[object, ...]:
    return (
        transition.register,
        transition.kind,
        transition.clock,
        transition.order,
        transition.value,
        transition.when,
        transition.trigger,
        transition.required_min_separation,
        transition.logical_offset,
    )


def _legacy_state_transitions(module: object) -> tuple[StateTransition, ...]:
    """Project compatibility state records without consulting ``module.transitions``."""

    # Normalized modules carry register clocks; raw modules use the first Flow clock available on
    # the operation and finally a stable inferred compatibility clock for callers that inspect a
    # module before normalization.
    from factorio_circuit.ir.semantic import Clock, ClockProvenance, Flow

    register_clocks = cast(dict[StateRegister, Clock], dict(getattr(module, "register_clocks", ())))

    def operation_clock(operation: object, register: StateRegister) -> Clock:
        clock = register_clocks.get(register)
        if clock is not None:
            return clock
        for candidate in (
            getattr(operation, "when", None),
            getattr(operation, "value", None),
        ):
            flow = getattr(candidate, "flow", None)
            if isinstance(flow, Flow):
                return flow.clock
        return Clock(
            f"{getattr(module, 'name', 'circuit')}:state:{register.name}",
            ClockProvenance.INFERRED,
        )

    result: list[StateTransition] = []
    for operation in getattr(module, "state_operations", ()):
        kind: str
        if isinstance(operation, AccumulatorAdd):
            kind = "add"
        elif isinstance(operation, AccumulatorClear):
            kind = "clear"
        elif isinstance(operation, FreezeSet):
            kind = "set"
        else:  # pragma: no cover - guarded by the StateOperation union
            continue
        value = getattr(operation, "value", None)
        value_flow = getattr(value, "flow", None)
        logical_offset = getattr(value_flow, "logical_offset", 0)
        result.append(
            StateTransition(
                register=operation.register,
                kind=kind,
                clock=operation_clock(operation, operation.register),
                order=operation.order,
                value=value,
                when=getattr(operation, "when", None),
                logical_offset=logical_offset,
                legacy=operation,
            )
        )
    for operation in getattr(module, "event_state_operations", ()):
        if isinstance(operation, FreezeCapture):
            result.append(
                StateTransition(
                    register=operation.register,
                    kind="capture",
                    clock=operation.trigger.clock,
                    order=operation.order,
                    value=operation.value,
                    trigger=operation.trigger,
                    required_min_separation=operation.required_min_separation,
                    legacy=operation,
                )
            )
    result.sort(key=lambda transition: transition.order)
    return tuple(result)


def periodic_state_operations(module: object) -> tuple[StateOperation, ...]:
    """Project authoritative periodic transitions for timing/lowering compatibility consumers."""

    existing = tuple(getattr(module, "state_operations", ()))
    if existing:
        state_transitions(module)  # Validate a possible canonical/legacy conflict first.
        return existing
    result: list[StateOperation] = []
    for transition in state_transitions(module):
        if transition.trigger is not None:
            continue
        if transition.kind == "add":
            if not isinstance(transition.register, AccumulatorRegister):
                raise ValueError("add transition requires an AccumulatorRegister")
            result.append(
                AccumulatorAdd(
                    transition.register,
                    transition.value,  # type: ignore[arg-type]
                    transition.when,  # type: ignore[arg-type]
                    transition.order,
                )
            )
        elif transition.kind == "clear":
            if not isinstance(transition.register, AccumulatorRegister):
                raise ValueError("clear transition requires an AccumulatorRegister")
            result.append(
                AccumulatorClear(
                    transition.register,
                    transition.when,  # type: ignore[arg-type]
                    transition.order,
                )
            )
        elif transition.kind == "set":
            if not isinstance(transition.register, FreezeRegister):
                raise ValueError("set transition requires a FreezeRegister")
            result.append(
                FreezeSet(
                    transition.register,
                    transition.value,  # type: ignore[arg-type]
                    transition.when,  # type: ignore[arg-type]
                    transition.order,
                )
            )
    return tuple(result)
