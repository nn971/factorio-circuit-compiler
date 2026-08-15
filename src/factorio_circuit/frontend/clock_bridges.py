"""Explicit clock-normalization primitives layered on the clocked public frontend."""

from __future__ import annotations

from typing import Any, cast

from factorio_circuit.events import EventCausalityError, EventCrossingError
from factorio_circuit.ir.clocks import EventMerge, GateClock
from factorio_circuit.ir.semantic import (
    Clock,
    ClockContract,
    ClockProvenance,
    EventInput,
    Flow,
    PayloadShape,
    ScalarValue,
    TemporalModality,
    VectorValue,
)

from .vector_circuit import Circuit as _Circuit
from .vector_circuit import Expr, SampleOnReference, ScalarEvent, VectorEvent
from .vector_expr import SignalsExpr

GatePredicateLike = Expr | SampleOnReference | ScalarEvent | int | bool
EventHandle = ScalarEvent | VectorEvent
VectorEventLike = VectorEvent | SignalsExpr


class Circuit(_Circuit):
    """Clocked circuit frontend with explicit bridge/derived-clock construction."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self._gate_clock_index: dict[tuple[EventInput, ScalarValue], GateClock] = {}
        self._gate_clock_counter = 0
        self._event_merge_index: dict[tuple[EventInput, ...], EventMerge] = {}
        self._event_merge_counter = 0
        self._hold_into_index: dict[tuple[VectorValue, EventInput], SampleOnReference] = {}
        self._hold_into_counter = 0

    def _derived_event_handle(self, source: EventInput) -> EventHandle:
        if source.payload_shape is PayloadShape.SCALAR:
            return ScalarEvent(self, source)
        return VectorEvent(self, source)

    def gate_clock(
        self,
        parent: ScalarEvent | VectorEvent,
        *,
        when: GatePredicateLike,
    ) -> ScalarEvent:
        """Return a shared derived subclock containing parent occurrences where ``when`` is true.

        The result is a unit-valued scalar Event handle. ``when`` must already be expressed on the
        parent occurrence clock (or be occurrence-invariant); implicit Level reads are intentionally
        rejected so clock crossings remain explicit.
        """

        if not isinstance(parent, (ScalarEvent, VectorEvent)):
            raise EventCausalityError("gate_clock parent must be a declared Event")
        if parent._circuit is not self:
            raise EventCausalityError("cannot gate an Event from a different Circuit")
        if parent.ir not in self._event_inputs:
            raise EventCausalityError("gate_clock parent must be declared by this Circuit")

        # The base frontend already accepts SampleOnReference and ScalarEvent at runtime; its old
        # ScalarLike annotation has not yet been widened to describe those Event-facing adapters.
        condition = self._coerce_scalar(cast(Any, when))
        flow = condition.flow
        if isinstance(flow, Flow):
            if flow.modality is TemporalModality.LEVEL:
                raise EventCrossingError(
                    "gate_clock cannot implicitly sample a Level; use sample_on(level, parent)"
                )
            if flow.modality is TemporalModality.EVENT and flow.clock != parent.clock:
                raise EventCausalityError(
                    "gate_clock predicate must use the parent occurrence clock"
                )

        key = (parent.ir, condition.ir)
        existing = self._gate_clock_index.get(key)
        if existing is not None:
            return ScalarEvent(self, existing)

        while True:
            name = f"gate{self._gate_clock_counter}"
            self._gate_clock_counter += 1
            if name not in self._used_names:
                self._used_names.add(name)
                break

        clock = Clock(
            identity=f"{self.name}:{name}:derived-gate",
            provenance=ClockProvenance.DERIVED,
            contract=parent.clock.contract,
        )
        try:
            gated = GateClock(
                name=name,
                payload_shape=PayloadShape.SCALAR,
                clock=clock,
                parent=parent.ir,
                predicate=condition.ir,
            )
        except (ValueError, EventCausalityError, EventCrossingError):
            self._used_names.remove(name)
            raise
        self._event_inputs.append(gated)
        self._gate_clock_index[key] = gated
        return ScalarEvent(self, gated)

    def event_merge(self, *parents: EventHandle) -> EventHandle:
        """Return the shared additive union of two or more same-shaped Event sources.

        Merge is set-like in its source identity: passing the same source repeatedly does not double
        count it. Nested merges are flattened, and parent order is canonicalized by declaration
        order so equivalent additive unions share one semantic object. Simultaneous contributions
        are coalesced by payload addition in reference semantics.
        """

        if len(parents) < 2:
            raise EventCausalityError("event_merge requires at least two Event sources")

        flattened: list[EventInput] = []

        def add_source(source: EventInput) -> None:
            if isinstance(source, EventMerge):
                for nested in source.parents:
                    add_source(nested)
                return
            if source not in flattened:
                flattened.append(source)

        for parent in parents:
            if not isinstance(parent, (ScalarEvent, VectorEvent)):
                raise EventCausalityError("event_merge operands must be declared Events")
            if parent._circuit is not self or parent.ir not in self._event_inputs:
                raise EventCausalityError("event_merge operands must belong to this Circuit")
            add_source(parent.ir)

        if len(flattened) == 1:
            return self._derived_event_handle(flattened[0])

        shape = flattened[0].payload_shape
        if any(source.payload_shape is not shape for source in flattened[1:]):
            raise EventCrossingError("event_merge operands must have one payload shape")

        declaration_order = {source: index for index, source in enumerate(self._event_inputs)}
        canonical = tuple(sorted(flattened, key=declaration_order.__getitem__))
        existing = self._event_merge_index.get(canonical)
        if existing is not None:
            return self._derived_event_handle(existing)

        while True:
            name = f"merge{self._event_merge_counter}"
            self._event_merge_counter += 1
            if name not in self._used_names:
                self._used_names.add(name)
                break

        clock = Clock(
            identity=f"{self.name}:{name}:derived-merge",
            provenance=ClockProvenance.DERIVED,
            contract=ClockContract(guaranteed_min_separation=1),
        )
        merged = EventMerge(
            name=name,
            payload_shape=shape,
            clock=clock,
            parents=canonical,
        )
        self._event_inputs.append(merged)
        self._event_merge_index[canonical] = merged
        return self._derived_event_handle(merged)

    def hold_into(
        self,
        source: VectorEventLike,
        target: EventHandle,
    ) -> SampleOnReference:
        """Hold the latest vector Event payload and expose it on ``target`` occurrences.

        This is an explicitly stateful cross-clock bridge. It elaborates immediately into one hidden
        ``FreezeRegister`` updated on the source clock plus one ``SampleOn`` crossing to the target
        clock. Equivalent bridges are interned, so the hidden state is paid for once.

        All activations at one timestamp observe the same old-state snapshot. Consequently, when a
        source and target activate simultaneously, the target observes the value held *before* that
        timestamp; the new source payload is visible at the next target activation.
        """

        if isinstance(source, VectorEvent):
            if source._circuit is not self or source.ir not in self._event_inputs:
                raise EventCausalityError("hold_into source must belong to this Circuit")
            value = source._as_signals()
        elif isinstance(source, SignalsExpr):
            self._require_owned(source)
            value = source
        else:
            raise EventCrossingError(
                "hold_into currently requires a vector Event source; scalar bridge state is not "
                "represented by the whole-vector state IR"
            )

        if not isinstance(target, (ScalarEvent, VectorEvent)):
            raise EventCausalityError("hold_into target must be a declared Event")
        if target._circuit is not self or target.ir not in self._event_inputs:
            raise EventCausalityError("hold_into target must belong to this Circuit")

        flow = value.flow
        if not isinstance(flow, Flow) or flow.modality is not TemporalModality.EVENT:
            raise EventCrossingError("hold_into source must be an Event expression")
        if flow.clock == target.clock:
            raise EventCrossingError(
                "hold_into requires distinct source and target clocks; use the Event value "
                "directly when no clock crossing is needed"
            )
        self._event_source(value.ir)  # Require one recoverable trigger before allocating state.

        key = (value.ir, target.ir)
        existing = self._hold_into_index.get(key)
        if existing is not None:
            return existing

        while True:
            state_name = f"hold{self._hold_into_counter}"
            self._hold_into_counter += 1
            if state_name not in self._used_names:
                break
        memory = self.freeze(state_name)
        memory.set(value, when=1)
        held = self.sample_on(memory.sample(), target)
        self._hold_into_index[key] = held
        return held
