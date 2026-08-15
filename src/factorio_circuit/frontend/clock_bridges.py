"""Explicit clock-normalization primitives layered on the clocked public frontend."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

from factorio_circuit.events import EventCausalityError, EventCrossingError
from factorio_circuit.ir.clocks import EventMerge, GateClock, SumInto
from factorio_circuit.ir.output import (
    MaterializedReturnValue,
    OutputMaterialization,
    OutputMaterializationPolicy,
)
from factorio_circuit.ir.semantic import (
    CircuitModule,
    Clock,
    ClockContract,
    ClockProvenance,
    Constant,
    EventInput,
    EventScalarFlow,
    EventVectorFlow,
    Flow,
    PayloadShape,
    ScalarValue,
    TemporalModality,
    VectorValue,
)

from .symbolic import CircuitBuildError
from .symbolic import OutputLike as _BaseOutputLike
from .vector_circuit import Circuit as _Circuit
from .vector_circuit import Expr, SampleOnReference, ScalarEvent, VectorEvent
from .vector_expr import SignalsExpr

GatePredicateLike = Expr | SampleOnReference | ScalarEvent | int | bool
EventHandle = ScalarEvent | VectorEvent
VectorEventLike = VectorEvent | SignalsExpr
OutputHandle = _BaseOutputLike | ScalarEvent | VectorEvent


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
        self._sum_into_index: dict[tuple[EventInput, EventInput], SumInto] = {}
        self._sum_into_counter = 0
        self._output_materializations: list[OutputMaterialization] = []

    def _derived_event_handle(self, source: EventInput) -> EventHandle:
        if source.payload_shape is PayloadShape.SCALAR:
            return ScalarEvent(self, source)
        return VectorEvent(self, source)

    def _default_output_policy(self, value: OutputHandle) -> OutputMaterializationPolicy:
        """Infer the initial boundary policy without changing internal Flow semantics."""

        if isinstance(value, (ScalarEvent, VectorEvent)):
            if isinstance(value.ir, (EventMerge, SumInto)):
                return OutputMaterializationPolicy.ZERO
            return OutputMaterializationPolicy.VALID
        if isinstance(value, SampleOnReference):
            return OutputMaterializationPolicy.VALID
        if isinstance(value, (Expr, SignalsExpr)):
            flow = value.flow
            if isinstance(flow, Flow) and flow.modality is TemporalModality.EVENT:
                ir = value.ir
                if isinstance(ir, (EventScalarFlow, EventVectorFlow)) and isinstance(
                    ir.source, (EventMerge, SumInto)
                ):
                    return OutputMaterializationPolicy.ZERO
                return OutputMaterializationPolicy.VALID
        return OutputMaterializationPolicy.HOLD

    def output(
        self,
        name: str,
        value: OutputHandle,
        *,
        policy: OutputMaterializationPolicy | None = None,
        valid_name: str | None = None,
    ) -> None:
        """Export one sparse semantic flow through an explicit dense-boundary contract.

        Defaults are ``HOLD`` for Level values, ``ZERO`` for direct additive ``EventMerge`` and
        ``SumInto`` streams, and ``VALID`` for other Event values.  Passing ``policy`` overrides the
        default.  ``VALID`` outputs expose a separate companion presence output whose default name
        is ``<payload-name>__valid``.
        """

        if policy is not None and not isinstance(policy, OutputMaterializationPolicy):
            raise CircuitBuildError("output policy must be an OutputMaterializationPolicy or None")
        inferred = self._default_output_policy(value)
        selected = inferred if policy is None else policy
        contract = OutputMaterialization(selected, valid_name)

        normalized: object = value
        if isinstance(value, ScalarEvent):
            normalized = value._as_expr()
        elif isinstance(value, VectorEvent):
            normalized = value._as_signals()

        # The base frontend owns output expression/name validation.  Stage 6 only adds the boundary
        # contract and widens direct Event handles into their ordinary Event expression wrappers.
        super().output(name, cast(Any, normalized))
        self._output_materializations.append(contract)

    def build(self) -> CircuitModule:
        """Freeze semantic IR while retaining materialization at the external boundary."""

        module = super().build()
        if len(self._output_materializations) != len(module.output.values):
            raise CircuitBuildError("output materialization metadata is inconsistent with outputs")
        return replace(
            module,
            output=MaterializedReturnValue(
                module.output.values,
                module.output.names,
                tuple(self._output_materializations),
            ),
        )

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

    def sum_into(self, source: VectorEvent, target: EventHandle) -> VectorEvent:
        """Accumulate source payloads into target-clock interval sums.

        ``sum_into(E, T)`` emits one packed vector Event at every occurrence of ``T``. Its
        payload is the additive sum of all ``E`` payloads since the previous target occurrence,
        including an ``E`` occurrence simultaneous with the current target. One hidden accumulator
        records the history requirement explicitly; equivalent bridges share that state.
        """

        if not isinstance(source, VectorEvent):
            raise EventCrossingError("sum_into currently requires a vector Event source")
        if source._circuit is not self or source.ir not in self._event_inputs:
            raise EventCausalityError("sum_into source must belong to this Circuit")
        if not isinstance(target, (ScalarEvent, VectorEvent)):
            raise EventCausalityError("sum_into target must be a declared Event")
        if target._circuit is not self or target.ir not in self._event_inputs:
            raise EventCausalityError("sum_into target must belong to this Circuit")
        if source.clock == target.clock:
            raise EventCrossingError(
                "sum_into requires distinct source and target clocks; use the Event value directly "
                "when no re-clocking is needed"
            )

        key = (source.ir, target.ir)
        existing = self._sum_into_index.get(key)
        if existing is not None:
            return VectorEvent(self, existing)

        while True:
            name = f"sum{self._sum_into_counter}"
            self._sum_into_counter += 1
            state_name = f"{name}_buffer"
            if name not in self._used_names and state_name not in self._used_names:
                break

        memory = self.accumulator(state_name)
        memory.add(source._as_signals())
        # Source addition is deliberately ordered before the target clear. The derived bridge
        # payload includes same-timestamp source contributions, and the clear drains them from the
        # hidden accumulator so the next interval starts empty.
        self._append_event_transition(
            "clear",
            memory._register,
            target.ir,
            None,
            Constant(1),
            None,
        )
        bridge = SumInto(
            name=name,
            payload_shape=PayloadShape.VECTOR,
            clock=target.clock,
            source=source.ir,
            target=target.ir,
            register=memory._register,
        )
        self._used_names.add(name)
        self._event_inputs.append(bridge)
        self._sum_into_index[key] = bridge
        return VectorEvent(self, bridge)
