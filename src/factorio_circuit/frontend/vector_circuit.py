"""Circuit builder hooks for runtime-open vectors and flow-local logical-step timing."""

from __future__ import annotations

from typing import cast

from factorio_circuit.events import EventCausalityError, EventCrossingError
from factorio_circuit.ir.physical import SignalId
from factorio_circuit.ir.semantic import (
    BinaryOp,
    Compare,
    DerivedValue,
    EventInput,
    Flow,
    PayloadShape,
    ScalarValue,
    Select,
    TemporalModality,
    VectorConstant,
    VectorInput,
    VectorValue,
)
from factorio_circuit.ir.semantic import Input as IRInput
from factorio_circuit.ir.state import StateRegister, StateTransition, VectorRegisterRead

from .reindex import FlowStepError, reindex_scalar
from .symbolic import AccumulatorReg as _BaseAccumulatorReg
from .symbolic import Circuit as _Circuit
from .symbolic import CircuitBuildError
from .symbolic import Expr as _BaseExpr
from .symbolic import FreezeReg as _BaseFreezeReg
from .symbolic import Input as _BaseInput
from .symbolic import SampleOnReference as _BaseSampleOnReference
from .symbolic import ScalarEvent as _BaseScalarEvent
from .symbolic import SignalsExpr as _BaseSignalsExpr
from .symbolic import SignalsInput as _BaseSignalsInput
from .symbolic import VectorEvent as _BaseVectorEvent
from .vector_expr import SignalsExpr


class Expr(_BaseExpr):
    """Public scalar expression with flow-local logical reindexing."""

    __slots__ = ()

    def step(self, n: int = 1) -> Expr:
        """Refer to this flow ``n`` logical clock occurrences later.

        ``step`` is pure logical reindexing: it leaves ``Circuit.now`` unchanged and never inserts a
        register or physical delay.  For Event values the later occurrence is consumed by Event
        reaction scheduling; Level values move their explicit sample leaves.
        """

        try:
            value = reindex_scalar(self._value, n)
        except FlowStepError as exc:
            raise CircuitBuildError(str(exc)) from exc
        if value is self._value:
            return self
        if isinstance(value, (BinaryOp, Compare, Select)):
            result = self._circuit._derived(value)
            return Expr(self._circuit, result.ir)
        return Expr(self._circuit, value)


class Input(_BaseInput, Expr):
    """Scalar Level source with both compatibility sampling and flow-local ``step``."""

    __slots__ = ()

    def sample(self) -> Expr:
        """Observe this external source at the circuit compatibility cursor."""

        offset = self._circuit.now.offset
        if offset == 0:
            return self
        return Expr(self._circuit, self._circuit._sample_scalar_input(self._source, offset))


class SignalsInput(_BaseSignalsInput, SignalsExpr):
    """Whole-vector Level source with compatibility sampling and flow-local ``step``."""

    __slots__ = ()

    def sample(self) -> SignalsExpr:
        """Observe this external vector at the current compatibility cursor."""

        offset = self._circuit.now.offset
        if offset == 0:
            return self
        return SignalsExpr(self._circuit, self._circuit._sample_vector_input(self._source, offset))


class ScalarEvent(_BaseScalarEvent):
    """Scalar Event source whose occurrence clock can be reindexed locally."""

    __slots__ = ()

    def _as_expr(self) -> Expr:
        return Expr(self._circuit, self._circuit._event_scalar_value(self._source))

    def step(self, n: int = 1) -> Expr:
        """Refer to this Event starting at its ``n``-th later occurrence."""

        return self._as_expr().step(n)


class VectorEvent(_BaseVectorEvent):
    """Vector Event source whose occurrence clock can be reindexed locally."""

    __slots__ = ()

    def step(self, n: int = 1) -> SignalsExpr:
        """Refer to this Event starting at its ``n``-th later occurrence."""

        return self._as_signals().step(n)


class SampleOnReference(_BaseSampleOnReference):
    """A Level snapshot reference that can be reindexed on its target Event clock."""

    __slots__ = ()

    def _as_value(self) -> Expr | SignalsExpr:
        if self._circuit is None:
            raise EventCrossingError("SampleOn reference is not attached to a Circuit")
        if self.payload_shape is PayloadShape.VECTOR:
            return SignalsExpr(self._circuit, self._crossing)
        return Expr(self._circuit, self._crossing)

    def step(self, n: int = 1) -> Expr | SignalsExpr:
        """Refer to this snapshot starting at the target Event's ``n``-th later occurrence."""

        return self._as_value().step(n)


class AccumulatorReg(_BaseAccumulatorReg):
    """Accumulator register whose logical observations retain vector operations."""

    def sample(self) -> SignalsExpr:
        """Observe the accumulator state at the current logical step."""

        read = VectorRegisterRead(
            register=self._register,
            offset=self._circuit.now.offset,
            order=self._circuit._next_state_order(self._register),
            name=self._register.name,
        )
        return SignalsExpr(self._circuit, read)

    @property
    def value(self) -> SignalsExpr:
        """Compatibility alias for :meth:`sample`; new code should use ``sample()``."""

        return self.sample()


class FreezeReg(_BaseFreezeReg):
    """Freeze register whose logical observations retain vector operations."""

    def sample(self) -> SignalsExpr:
        """Observe the held state at the current logical step."""

        read = VectorRegisterRead(
            register=self._register,
            offset=self._circuit.now.offset,
            order=self._circuit._next_state_order(self._register),
            name=self._register.name,
        )
        return SignalsExpr(self._circuit, read)

    @property
    def value(self) -> SignalsExpr:
        """Compatibility alias for :meth:`sample`; new code should use ``sample()``."""

        return self.sample()


def _event_occurrence_offsets(value: object, seen: set[int] | None = None) -> set[int]:
    """Collect effective Event occurrence offsets, respecting explicit reindex boundaries."""

    if value is None:
        return set()
    if seen is None:
        seen = set()
    if id(value) in seen:
        return set()
    seen.add(id(value))

    flow = getattr(value, "flow", None)
    if isinstance(flow, Flow) and flow.modality is TemporalModality.EVENT:
        if flow.logical_offset < 0:
            raise EventCausalityError("Event logical occurrence offsets must be non-negative")
        if flow.logical_offset > 0:
            # A positive root offset is an explicit ``step`` boundary.  Its children are evaluated
            # at the reaction selected by this boundary, so their source-local zero offsets do not
            # represent an additional clock crossing.
            return {flow.logical_offset}

    offsets: set[int] = set()
    children = (
        "left",
        "right",
        "condition",
        "when_true",
        "when_false",
        "vector",
        "scalar",
    )
    found_child = False
    for field_name in children:
        child = getattr(value, field_name, None)
        if child is not None:
            found_child = True
            offsets.update(_event_occurrence_offsets(child, seen))
    if offsets:
        return offsets
    if isinstance(flow, Flow) and flow.modality is TemporalModality.EVENT:
        return {flow.logical_offset}
    return set() if found_child else offsets


class Circuit(_Circuit):
    """Symbolic circuit whose compatibility cursor is measured in logical steps."""

    def input(self, name: str) -> Input:
        self._claim_name(name, "input")
        value = IRInput(name)
        self._inputs.append(value)
        return Input(self, value)

    def signals(self, name: str) -> SignalsInput:
        self._claim_name(name, "input")
        value = VectorInput(name)
        self._vector_inputs.append(value)
        return SignalsInput(self, value)

    def event(self, name: str, *, guaranteed_min_separation: int) -> ScalarEvent:
        return cast(
            ScalarEvent,
            self._declare_event(
                name,
                PayloadShape.SCALAR,
                guaranteed_min_separation,
                ScalarEvent,
            ),
        )

    def signal_event(self, name: str, *, guaranteed_min_separation: int) -> VectorEvent:
        return cast(
            VectorEvent,
            self._declare_event(
                name,
                PayloadShape.VECTOR,
                guaranteed_min_separation,
                VectorEvent,
            ),
        )

    def sample_on(
        self,
        source: _BaseInput | _BaseExpr | _BaseSignalsExpr,
        target: _BaseScalarEvent | _BaseVectorEvent,
    ) -> SampleOnReference:
        reference = super().sample_on(source, target)
        return SampleOnReference(reference.ir, self)

    def constant_signals(self, signals: dict[SignalId, int]) -> SignalsExpr:
        normalized: list[tuple[SignalId, int]] = []
        for signal, value in signals.items():
            if not isinstance(signal, SignalId):
                raise CircuitBuildError("constant_signals keys must be SignalId values")
            if isinstance(value, bool) or not isinstance(value, int):
                raise CircuitBuildError("constant_signals values must be integers")
            if value != 0:
                normalized.append((signal, value))
        normalized.sort(key=lambda item: (item[0].kind, item[0].name))
        return SignalsExpr(self, VectorConstant(tuple(normalized)))

    def accumulator(self, name: str | None = None) -> AccumulatorReg:
        return AccumulatorReg(self, name=name)

    def freeze(self, name: str | None = None) -> FreezeReg:
        return FreezeReg(self, name=name)

    def _derived(self, value: DerivedValue) -> Expr:
        """Keep scalar derived results on the public flow-local Expr surface."""

        result = super()._derived(value)
        return Expr(self, result.ir)

    def _append_event_transition(
        self,
        kind: str,
        register: StateRegister,
        trigger: EventInput,
        value: VectorValue | None,
        when: ScalarValue | None,
        required_min_separation: int | None,
    ) -> None:
        """Create one Event transition at the operands' common occurrence offset."""

        for candidate, label in ((value, "value"), (when, "condition")):
            flow = getattr(candidate, "flow", None)
            if (
                isinstance(flow, Flow)
                and flow.modality is TemporalModality.EVENT
                and flow.clock != trigger.clock
            ):
                raise EventCausalityError(f"Event transition {label} must use the trigger clock")

        offsets = _event_occurrence_offsets(value) | _event_occurrence_offsets(when)
        if len(offsets) > 1:
            raise EventCausalityError(
                "Event transition operands must use one logical occurrence offset"
            )
        logical_offset = next(iter(offsets), 0)
        self._transitions.append(
            StateTransition(
                register=register,
                kind=kind,
                clock=trigger.clock,
                order=self._next_state_order(register),
                value=value,
                when=when,
                trigger=trigger,
                required_min_separation=required_min_separation,
                logical_offset=logical_offset,
            )
        )
        self._event_capture_registers.add(register)

    def step(self, n: int = 1) -> None:
        """Advance the legacy circuit-wide logical observation cursor by ``n`` steps.

        New code should prefer ``value.step(n)`` so logical indexing is local to the value being
        reindexed.  This method remains during migration to preserve existing circuit programs.
        """

        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            raise CircuitBuildError("step(n) requires a non-negative integer")
        self._freshness += n

    def step_until(self, n: int) -> None:
        """Advance the compatibility cursor to absolute logical step ``n``."""

        if isinstance(n, bool) or not isinstance(n, int) or n < self._freshness:
            raise CircuitBuildError(
                f"step_until(n) requires an integer n >= current logical step {self._freshness}"
            )
        self._freshness = n

    def tick(self, n: int = 1) -> None:
        """Reserve the physical-tick spelling for future explicit scheduling controls."""

        del n
        raise CircuitBuildError(
            "Circuit.tick() is reserved for future physical-tick control; use Circuit.step() "
            "to advance logical time"
        )

    def tick_until(self, n: int) -> None:
        """Reject the former logical-time spelling; use :meth:`step_until`."""

        del n
        raise CircuitBuildError(
            "Circuit.tick_until() no longer denotes logical time; use Circuit.step_until()"
        )
