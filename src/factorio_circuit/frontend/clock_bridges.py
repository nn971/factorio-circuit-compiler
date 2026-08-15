"""Explicit clock-normalization primitives layered on the clocked public frontend."""

from __future__ import annotations

from typing import Any, cast

from factorio_circuit.events import EventCausalityError, EventCrossingError
from factorio_circuit.ir.clocks import GateClock
from factorio_circuit.ir.semantic import (
    Clock,
    ClockProvenance,
    EventInput,
    Flow,
    PayloadShape,
    ScalarValue,
    TemporalModality,
)

from .vector_circuit import Circuit as _Circuit
from .vector_circuit import Expr, SampleOnReference, ScalarEvent, VectorEvent

GatePredicateLike = Expr | SampleOnReference | ScalarEvent | int | bool


class Circuit(_Circuit):
    """Clocked circuit frontend with explicit bridge/derived-clock construction."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self._gate_clock_index: dict[tuple[EventInput, ScalarValue], GateClock] = {}
        self._gate_clock_counter = 0

    def gate_clock(
        self,
        parent: ScalarEvent | VectorEvent,
        *,
        when: GatePredicateLike,
    ) -> ScalarEvent:
        """Return a shared derived subclock containing parent occurrences where ``when`` is true.

        The result is a unit-valued scalar Event handle.  ``when`` must already be expressed on the
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
