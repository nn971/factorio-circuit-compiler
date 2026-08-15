"""Physical realization of ordinary Event-driven additive state.

``SumInto`` owns an interval accumulator because it is a cross-clock bridge. A logical accumulator
already driven by an Event clock needs no such intermediate bridge: gate the Event payload once and
feed it directly into the destination register's feedback cell. This layer provides that fusion for
the minimal, compositional case of one unconditional Event ``add`` transition per user accumulator.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from factorio_circuit.analysis.state_timing import StateTimingPlan
from factorio_circuit.events import EventCompilationError
from factorio_circuit.ir.abstract_physical import (
    AbstractPhysicalCircuit,
    ArithmeticCombinator,
    Connector,
    Endpoint,
    Operand,
)
from factorio_circuit.ir.semantic import CircuitModule, Constant
from factorio_circuit.ir.state import AccumulatorRegister, StateTransition
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedVector
from factorio_circuit.lowering.occurrence_reindex_physical import (
    OccurrenceReindexPhysicalLowerer,
)


@dataclass(frozen=True, slots=True)
class _EventAccumulatorCell:
    register: AccumulatorRegister
    entity: int
    memory_net: int


class EventAccumulatorPhysicalLowerer(OccurrenceReindexPhysicalLowerer):
    """Occurrence-aware lowerer with direct Event-to-accumulator state fusion."""

    def __init__(self, module: CircuitModule, *, state_timing: StateTimingPlan) -> None:
        super().__init__(module, state_timing=state_timing)
        self._event_accumulator_cells: dict[AccumulatorRegister, _EventAccumulatorCell] = {}

    @property
    def _user_event_accumulators(self) -> tuple[AccumulatorRegister, ...]:
        return tuple(
            register
            for register in self.module.state_registers
            if isinstance(register, AccumulatorRegister)
            and register not in self._sum_into_by_register
        )

    def _base_state_view(self) -> tuple[CircuitModule, tuple[StateTransition, ...]]:
        user_accumulators = set(self._user_event_accumulators)
        module = replace(
            self.module,
            state_registers=tuple(
                register
                for register in self.module.state_registers
                if register not in user_accumulators
            ),
            transitions=tuple(
                transition
                for transition in self._event_transitions
                if transition.register not in user_accumulators
            ),
        )
        transitions = tuple(
            transition
            for transition in self._event_transitions
            if transition.register not in user_accumulators
        )
        return module, transitions

    def _with_base_state(self, action: str) -> None:
        original_module = self.module
        original_transitions = self._event_transitions
        base_module, base_transitions = self._base_state_view()
        self.module = base_module
        self._event_transitions = base_transitions
        try:
            getattr(super(), action)()
        finally:
            self.module = original_module
            self._event_transitions = original_transitions

    def _check_clocked_scope(self) -> None:
        user_accumulators = self._user_event_accumulators
        if not user_accumulators:
            super()._check_clocked_scope()
            return

        self._with_base_state("_check_clocked_scope")
        by_register: dict[AccumulatorRegister, list[StateTransition]] = {
            register: [] for register in user_accumulators
        }
        for transition in self._event_transitions:
            if (
                isinstance(transition.register, AccumulatorRegister)
                and transition.register in by_register
            ):
                by_register[transition.register].append(transition)

        for register, transitions in by_register.items():
            if len(transitions) != 1:
                raise EventCompilationError(
                    f"Event accumulator {register.name!r} requires exactly one physical add "
                    "transition"
                )
            transition = transitions[0]
            if (
                transition.kind != "add"
                or transition.trigger is None
                or transition.value is None
                or not isinstance(transition.when, Constant)
                or transition.when.value != 1
            ):
                raise EventCompilationError(
                    "direct physical Event accumulators currently require one unconditional add"
                )
            if transition.logical_offset != 0:
                raise EventCompilationError(
                    "Event accumulator occurrence offsets must be normalized to a tail clock"
                )

    def _reserve_event_state_outputs(self) -> None:
        user_accumulators = self._user_event_accumulators
        if not user_accumulators:
            super()._reserve_event_state_outputs()
            return

        self._with_base_state("_reserve_event_state_outputs")
        for register in user_accumulators:
            entity = self._take_entity_id()
            input_endpoint = Endpoint(entity, Connector.INPUT)
            output_endpoint = Endpoint(entity, Connector.OUTPUT)
            memory_net = self._new_net(
                (),
                input_endpoint,
                label=f"Event Accumulator {register.name}: memory",
                carries_dynamic_vector=True,
            )
            self._attach(memory_net, output_endpoint)
            self.state_memory_nets[register.name] = memory_net
            self.state_outputs[register.name] = RealizedVector(memory_net, 0)
            self._event_accumulator_cells[register] = _EventAccumulatorCell(
                register,
                entity,
                memory_net,
            )

    def _create_event_state_components(self) -> None:
        user_accumulators = self._user_event_accumulators
        if not user_accumulators:
            super()._create_event_state_components()
            return

        self._with_base_state("_create_event_state_components")
        by_register = {
            transition.register: transition
            for transition in self._event_transitions
            if transition.register in user_accumulators
        }
        for register in user_accumulators:
            self._lower_event_accumulator(by_register[register])

    def _lower_event_accumulator(self, transition: StateTransition) -> None:
        assert isinstance(transition.register, AccumulatorRegister)
        assert transition.value is not None
        cell = self._event_accumulator_cells[transition.register]

        # The existing Event gate turns absence into the additive identity while preserving the
        # payload selected at the semantic occurrence. The destination feedback cell can then run
        # every game tick and absorb one gated contribution per tick without an intermediate bridge.
        gated, _valid = self._gate_vector_event(transition.value)
        self._add_net_conflict(
            cell.memory_net,
            gated.net,
            f"Event Accumulator {transition.register.name}: memory/contribution isolation",
        )
        accumulator = ArithmeticCombinator(
            id=cell.entity,
            operation="+",
            left=Operand(each=True, nets=(cell.memory_net,)),
            right=Operand(each=True, nets=(gated.net,)),
            output_each=True,
            description=f"Event Accumulator {transition.register.name}: add gated occurrence",
        )
        self.circuit.entities.append(accumulator)
        self._attach(gated.net, Endpoint(accumulator.id, Connector.INPUT))


def lower_event_accumulator_physical(
    module: CircuitModule,
    *,
    state_timing: StateTimingPlan,
) -> AbstractPhysicalCircuit:
    """Lower the implemented Event/bridge slice plus direct additive Event state."""

    return EventAccumulatorPhysicalLowerer(module, state_timing=state_timing).lower()


__all__ = ["EventAccumulatorPhysicalLowerer", "lower_event_accumulator_physical"]
