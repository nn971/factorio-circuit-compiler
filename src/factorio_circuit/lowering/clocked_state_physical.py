"""Event-clocked state extension for the physical clocked-flow lowerer.

This module keeps Event state in the same physical lowering model as feed-forward Event flows:
payload paths evaluate continuously, activation tokens identify semantic occurrences, and a state
commit is a one-tick Factorio cell gated by the delayed activation token.

Supported stateful cells are deliberately structural rather than generic. ``FreezeRegister``
capture and unconditional Event-set transitions use a valid-gated sample/hold cell. ``SumInto``
recognizes its compiler-owned ``AccumulatorRegister`` plus canonical source-add/target-clear
transitions and lowers them to the right-closed accumulator/snapshot topology confirmed by the
in-game timing probe.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from factorio_circuit.analysis.latency import FACTORIO_LATENCY
from factorio_circuit.analysis.state_timing import StateTimingPlan
from factorio_circuit.events import EventCompilationError
from factorio_circuit.ir.abstract_physical import (
    AbstractNet,
    AbstractPhysicalCircuit,
    ArithmeticCombinator,
    Connector,
    DeciderCombinator,
    Endpoint,
    Operand,
)
from factorio_circuit.ir.clocks import SumInto
from factorio_circuit.ir.semantic import (
    CircuitModule,
    Constant,
    EventInput,
    EventVectorFlow,
    Flow,
    PayloadShape,
    TemporalModality,
    VectorValue,
)
from factorio_circuit.ir.state import (
    AccumulatorRegister,
    FreezeRegister,
    StateTransition,
    VectorRegisterRead,
    state_transitions,
)
from factorio_circuit.lowering.clocked_physical import ClockedPhysicalLowerer
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedVector
from factorio_circuit.target.factorio.signals import SIGNAL_EVERYTHING


@dataclass(frozen=True, slots=True)
class _EventFreezeCell:
    register: FreezeRegister
    feedback_entity: int
    memory_net: int


class StatefulClockedPhysicalLowerer(ClockedPhysicalLowerer):
    """Clocked lowerer with executable Event-triggered state and explicit clock bridges."""

    def __init__(self, module: CircuitModule, *, state_timing: StateTimingPlan) -> None:
        super().__init__(module, state_timing=state_timing)
        self._event_transitions = state_transitions(self.module)
        self._event_freeze_cells: dict[str, _EventFreezeCell] = {}
        self._sum_into_bridges = tuple(
            source for source in self.module.event_inputs if isinstance(source, SumInto)
        )
        self._sum_into_by_register: dict[AccumulatorRegister, SumInto] = {
            bridge.register: bridge for bridge in self._sum_into_bridges
        }
        if len(self._sum_into_by_register) != len(self._sum_into_bridges):
            raise EventCompilationError("multiple SumInto bridges cannot share one accumulator")
        self._sum_into_payloads: dict[SumInto, RealizedVector] = {}

    def lower(self) -> AbstractPhysicalCircuit:
        self._check_clocked_scope()
        self._create_input_markers()
        self._create_event_input_markers()
        self._reserve_event_state_outputs()
        self._create_event_state_components()
        self._create_materialized_outputs()
        self.circuit.nets = [
            AbstractNet(
                id=net_id,
                signals=builder.signals,
                endpoints=tuple(builder.endpoints),
                label=builder.label,
                fixed_signals=builder.fixed_signals,
                carries_dynamic_vector=builder.carries_dynamic_vector,
            )
            for net_id, builder in sorted(self.net_builders.items())
        ]
        self.circuit.validate()
        return self.circuit

    def _create_event_input_markers(self) -> None:
        """Expose only true external Event sources in the physical ABI.

        Derived ``EventInput`` subclasses live in ``module.event_inputs`` so reference simulation
        can schedule them structurally, but they are compiler-owned physical logic.  In particular a
        ``SumInto`` deliberately reuses its target clock, so exposing it as another external source
        would both create a bogus payload port and duplicate that target's valid token.
        """

        original = self.module
        external_inputs = tuple(
            source for source in original.event_inputs if type(source) is EventInput
        )
        self.module = replace(original, event_inputs=external_inputs)
        try:
            super()._create_event_input_markers()
        finally:
            self.module = original

    @staticmethod
    def _is_true_condition(value: object) -> bool:
        return isinstance(value, Constant) and value.value == 1

    def _validate_sum_into_transitions(
        self,
        bridge: SumInto,
        transitions: list[StateTransition],
    ) -> None:
        ordered = sorted(transitions, key=lambda transition: transition.order)
        if len(ordered) != 2 or [transition.kind for transition in ordered] != ["add", "clear"]:
            raise EventCompilationError(
                f"SumInto {bridge.name!r} requires exactly source-add then target-clear transitions"
            )
        add, clear = ordered
        if add.trigger != bridge.source or add.clock != bridge.source.clock:
            raise EventCompilationError(
                f"SumInto {bridge.name!r} add transition must use its source clock"
            )
        if clear.trigger != bridge.target or clear.clock != bridge.target.clock:
            raise EventCompilationError(
                f"SumInto {bridge.name!r} clear transition must use its target clock"
            )
        if add.logical_offset != 0 or clear.logical_offset != 0:
            raise EventCompilationError(
                "SumInto occurrence offsets require an explicit temporal buffer"
            )
        if add.order >= clear.order:
            raise EventCompilationError(
                f"SumInto {bridge.name!r} must order source addition before target clear"
            )
        if (
            not isinstance(add.value, EventVectorFlow)
            or add.value.source != bridge.source
            or not self._is_true_condition(add.when)
        ):
            raise EventCompilationError(
                f"SumInto {bridge.name!r} add transition must add the complete source payload"
            )
        if clear.value is not None or not self._is_true_condition(clear.when):
            raise EventCompilationError(
                f"SumInto {bridge.name!r} clear transition must unconditionally drain the buffer"
            )

    def _check_clocked_scope(self) -> None:
        if self.state_timing.unsupported_crossings:
            raise EventCompilationError(
                "physical Event lowering encountered an unsupported cross-clock state dependency"
            )

        if not self.module.state_registers:
            # Preserve all feed-forward checks from the established clocked lowerer.
            super()._check_clocked_scope()
            return

        if self.module.state_operations:
            raise EventCompilationError(
                "physical clocked state lowering does not yet mix periodic state operations with "
                "Event-triggered state"
            )

        transitions_by_register: dict[object, list[StateTransition]] = {}
        registered = set(self.module.state_registers)
        for transition in self._event_transitions:
            if transition.register not in registered:
                raise EventCompilationError(
                    "physical Event state transition targets an unlisted register"
                )
            transitions_by_register.setdefault(transition.register, []).append(transition)

        for register in self.module.state_registers:
            transitions = transitions_by_register.get(register, [])
            bridge = self._sum_into_by_register.get(register)  # type: ignore[arg-type]
            if bridge is not None:
                self._validate_sum_into_transitions(bridge, transitions)
                continue

            if not isinstance(register, FreezeRegister):
                raise EventCompilationError(
                    "physical Event state lowering supports FreezeRegister Event updates and "
                    "compiler-owned SumInto accumulators only"
                )
            if len(transitions) != 1:
                raise EventCompilationError(
                    f"FreezeReg {register.name!r} requires exactly one Event update transition "
                    "for physical lowering"
                )
            transition = transitions[0]
            if transition.trigger is None:
                raise EventCompilationError(
                    "physical clocked state lowering currently supports Event-triggered freeze "
                    "updates only"
                )
            if transition.kind not in {"capture", "set"}:
                raise EventCompilationError(
                    "physical clocked state lowering currently supports FreezeReg.capture_on and "
                    "unconditional Event FreezeReg.set only; "
                    f"unsupported Event transition kind {transition.kind!r}"
                )
            if transition.kind == "set" and (
                transition.value is None or not self._is_true_condition(transition.when)
            ):
                raise EventCompilationError(
                    "physical Event FreezeReg.set currently requires an unconditional when=1 update"
                )
            if transition.logical_offset != 0:
                raise EventCompilationError(
                    "nonzero Event state-transition occurrence offsets require an explicit "
                    "temporal buffer"
                )

    def _reserve_event_state_outputs(self) -> None:
        """Reserve all user-visible freeze memories before realizing transition expressions.

        SumInto accumulators are different: their feedback bus is created lazily with the bridge
        payload because no user-visible state read can name that compiler-owned register.
        """

        for register in self.module.state_registers:
            if register in self._sum_into_by_register:
                continue
            assert isinstance(register, FreezeRegister)  # checked above
            feedback_id = self._take_entity_id()
            feedback_input = Endpoint(feedback_id, Connector.INPUT)
            feedback_output = Endpoint(feedback_id, Connector.OUTPUT)
            memory_net = self._new_net(
                (),
                feedback_input,
                label=f"Event FreezeReg {register.name}: memory",
                carries_dynamic_vector=True,
            )
            self._attach(memory_net, feedback_output)
            self.state_memory_nets[register.name] = memory_net
            self.state_outputs[register.name] = RealizedVector(memory_net, 0)
            self._event_freeze_cells[register.name] = _EventFreezeCell(
                register,
                feedback_id,
                memory_net,
            )

    def _create_event_state_components(self) -> None:
        by_register = {
            transition.register: transition
            for transition in self._event_transitions
            if transition.register not in self._sum_into_by_register
        }
        for register in self.module.state_registers:
            if register in self._sum_into_by_register:
                continue
            assert isinstance(register, FreezeRegister)
            self._lower_event_freeze(by_register[register])

    def _freeze_source(self, transition: StateTransition) -> RealizedVector:
        if transition.value is not None:
            return self.realize_vector(transition.value)

        trigger = transition.trigger
        assert trigger is not None
        if trigger.payload_shape is not PayloadShape.VECTOR:
            raise EventCompilationError("scalar Event capture requires an explicit vector value")
        if isinstance(trigger, SumInto):
            return self._realize_sum_into(trigger)
        try:
            return self._event_vector_payloads[trigger]
        except KeyError as exc:  # pragma: no cover - semantic validation should catch this
            raise EventCompilationError(
                f"Event capture trigger {trigger.name!r} has no physical vector payload"
            ) from exc

    def _lower_event_freeze(self, transition: StateTransition) -> None:
        assert isinstance(transition.register, FreezeRegister)
        cell = self._event_freeze_cells[transition.register.name]
        source = self._freeze_source(transition)
        valid = self._clock_at(transition.clock, source.phase)

        if source.net == cell.memory_net:
            # ``state := state`` is a semantic no-op.  A normal gated update would connect a
            # decider's input and output onto the same bus and produce an unnecessary electrical
            # recurrence.  Realize the no-op as the ordinary one-tick vector memory cell instead.
            memory = ArithmeticCombinator(
                id=cell.feedback_entity,
                operation="+",
                left=Operand(each=True, nets=(cell.memory_net,)),
                right=Operand(constant=0),
                output_each=True,
                description=f"Event FreezeReg {transition.register.name}: identity memory",
            )
            self.circuit.entities.append(memory)
            return

        self._add_net_conflict(
            source.net,
            valid.net,
            f"Event FreezeReg {transition.register.name}: update data/valid isolation",
        )
        self._add_net_conflict(
            cell.memory_net,
            valid.net,
            f"Event FreezeReg {transition.register.name}: memory/valid isolation",
        )

        update = DeciderCombinator(
            id=self._take_entity_id(),
            comparator="!=",
            left=Operand(signal=valid.signal, nets=(valid.net,)),
            right=Operand(constant=0),
            output_signal=SIGNAL_EVERYTHING,
            output_copy_count_from_input=True,
            copy_count_nets=(source.net,),
            description=f"Event FreezeReg {transition.register.name}: update on valid",
        )
        self.circuit.entities.append(update)
        update_input = Endpoint(update.id, Connector.INPUT)
        self._attach(source.net, update_input)
        self._attach(valid.net, update_input)
        self._attach(cell.memory_net, Endpoint(update.id, Connector.OUTPUT))

        feedback = DeciderCombinator(
            id=cell.feedback_entity,
            comparator="==",
            left=Operand(signal=valid.signal, nets=(valid.net,)),
            right=Operand(constant=0),
            output_signal=SIGNAL_EVERYTHING,
            output_copy_count_from_input=True,
            copy_count_nets=(cell.memory_net,),
            description=f"Event FreezeReg {transition.register.name}: retain while invalid",
        )
        self.circuit.entities.append(feedback)
        self._attach(valid.net, Endpoint(feedback.id, Connector.INPUT))

    def _realize_sum_into(self, bridge: SumInto) -> RealizedVector:
        cached = self._sum_into_payloads.get(bridge)
        if cached is not None:
            return cached
        if bridge.source not in self._event_vector_payloads:
            raise EventCompilationError(
                "physical SumInto currently requires a directly declared vector Event source"
            )
        if type(bridge.target) is not EventInput:
            raise EventCompilationError(
                "physical SumInto currently requires a directly declared target Event clock"
            )

        source_event = EventVectorFlow(
            bridge.source,
            Flow(
                reference=bridge.source,
                payload_shape=PayloadShape.VECTOR,
                modality=TemporalModality.EVENT,
                clock=bridge.source.clock,
            ),
        )
        source_payload, _ = self._gate_vector_event(source_event)
        target_valid = self._clock_at(bridge.target.clock, source_payload.phase)
        accumulator_net = source_payload.net
        accumulator = self.net_builders[accumulator_net]
        self._add_net_conflict(
            accumulator_net,
            target_valid.net,
            f"SumInto {bridge.name}: accumulator/target-valid isolation",
        )

        # Probe-confirmed topology: the source contribution and previous feedback share one data
        # bus.  On a target occurrence the snapshot sees that complete bus while the feedback path
        # is suppressed, so a simultaneous source occurrence belongs to (previous_target, target]
        # and the following interval starts empty.
        feedback = DeciderCombinator(
            id=self._take_entity_id(),
            comparator="==",
            left=Operand(signal=target_valid.signal, nets=(target_valid.net,)),
            right=Operand(constant=0),
            output_signal=SIGNAL_EVERYTHING,
            output_copy_count_from_input=True,
            copy_count_nets=(accumulator_net,),
            description=f"SumInto {bridge.name}: retain interval sum until target",
        )
        self.circuit.entities.append(feedback)
        feedback_input = Endpoint(feedback.id, Connector.INPUT)
        self._attach(accumulator_net, feedback_input)
        self._attach(target_valid.net, feedback_input)
        self._attach(accumulator_net, Endpoint(feedback.id, Connector.OUTPUT))

        snapshot = DeciderCombinator(
            id=self._take_entity_id(),
            comparator="!=",
            left=Operand(signal=target_valid.signal, nets=(target_valid.net,)),
            right=Operand(constant=0),
            output_signal=SIGNAL_EVERYTHING,
            output_copy_count_from_input=True,
            copy_count_nets=(accumulator_net,),
            description=f"SumInto {bridge.name}: snapshot right-closed interval",
        )
        self.circuit.entities.append(snapshot)
        snapshot_input = Endpoint(snapshot.id, Connector.INPUT)
        self._attach(accumulator_net, snapshot_input)
        self._attach(target_valid.net, snapshot_input)
        snapshot_net = self._new_net(
            accumulator.signals,
            Endpoint(snapshot.id, Connector.OUTPUT),
            label=f"SumInto {bridge.name}: target-clock payload",
            fixed_signals=accumulator.fixed_signals,
            carries_dynamic_vector=accumulator.carries_dynamic_vector,
        )
        phase = source_payload.phase + FACTORIO_LATENCY.state_transition_latency("capture")
        result = RealizedVector(snapshot_net, phase)
        self._sum_into_payloads[bridge] = result
        return result

    def realize_vector(self, value: VectorValue) -> RealizedVector:
        if isinstance(value, EventVectorFlow) and isinstance(value.source, SumInto):
            cached = self.vector_memo.get(id(value))
            if cached is not None:
                return cached
            self._event_flow(value)
            result = self._realize_sum_into(value.source)
            self.vector_memo[id(value)] = result
            return result
        if isinstance(value, VectorRegisterRead):
            cached = self.vector_memo.get(id(value))
            if cached is not None:
                return cached
            if value.offset != 0:
                raise EventCompilationError(
                    "nonzero logical offsets of Event-updated state require an explicit temporal "
                    "buffer"
                )
            try:
                state = self.state_outputs[value.register.name]
            except KeyError as exc:
                raise EventCompilationError(
                    f"Event state register {value.register.name!r} was not reserved"
                ) from exc
            result = RealizedVector(state.net, 0)
            self.vector_memo[id(value)] = result
            return result
        return super().realize_vector(value)


def lower_stateful_clocked_physical(
    module: CircuitModule,
    *,
    state_timing: StateTimingPlan,
) -> AbstractPhysicalCircuit:
    """Lower feed-forward Event flows plus the supported Event state/bridge slice."""

    return StatefulClockedPhysicalLowerer(
        module,
        state_timing=state_timing,
    ).lower()


__all__ = ["StatefulClockedPhysicalLowerer", "lower_stateful_clocked_physical"]
