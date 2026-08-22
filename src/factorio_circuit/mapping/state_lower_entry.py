"""Public entry point for periodic-state physical lowering.

The mature lowering stack may ask to align an operand to a phase it already occupies. Those calls
are semantically no-ops and must not consume a second planned delivery from the temporal mapping
plan. The mapped backend also has to honor the dense Level output boundary: a display observes its
network continuously, so a coherent framebuffer sampled at one phase must be held while the next
logical occurrence settles internally.
"""

from __future__ import annotations

from dataclasses import dataclass

from factorio_circuit.ir.abstract_physical import (
    AbstractPhysicalCircuit,
    Connector,
    DeciderCombinator,
    Endpoint,
)
from factorio_circuit.ir.semantic import CircuitModule
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedValue, RealizedVector
from factorio_circuit.lowering.open_vector import VectorLowerer
from factorio_circuit.target.factorio.signals import SIGNAL_EVERYTHING

from .plan import RealizationPlan
from .problem import MappingProblem
from .state_lower import (
    PeriodicStatePhysicalLoweringResult as _BasePeriodicStatePhysicalLoweringResult,
    _MappedPeriodicStateLowerer,
)
from .state_templates import StateCellCandidate
from .templates import ImplementationCandidate


@dataclass(frozen=True, slots=True)
class PeriodicStatePhysicalLoweringResult:
    """Mapped physical result with currently unpriced boundary hardware made explicit."""

    circuit: AbstractPhysicalCircuit
    fixed_source_entities: int
    candidate_internal_entities: int
    output_materialization_entities: int
    planned_cost: int

    @property
    def emitted_combinators(self) -> int:
        return self.circuit.combinator_count

    @property
    def accounted_cost(self) -> int:
        return (
            self.planned_cost
            + self.fixed_source_entities
            + self.candidate_internal_entities
            + self.output_materialization_entities
        )

    @property
    def unexplained_cost_gap(self) -> int:
        return self.emitted_combinators - self.accounted_cost

    @property
    def cost_exact_after_known_surcharges(self) -> bool:
        return self.unexplained_cost_gap == 0


class _BoundarySafeMappedPeriodicStateLowerer(_MappedPeriodicStateLowerer):
    """Keep mapped semantic uses exact and expose coherent public Level frames."""

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.output_materialization_entities = 0

    def delay_to(self, value: RealizedValue, target_phase: int) -> RealizedValue:
        if value.phase == target_phase:
            return value
        return super().delay_to(value, target_phase)

    def delay_vector_to(self, value: RealizedVector, target_phase: int) -> RealizedVector:
        if value.phase == target_phase:
            return value
        return super().delay_vector_to(value, target_phase)

    def _hold_framebuffer(self, payload: RealizedVector) -> RealizedVector:
        """Capture one coherent framebuffer once per mapped period and hold it continuously."""

        if self.commit_clock_net is None or self.commit_ready_net is None:
            raise AssertionError("periodic commit resource must exist before output materialization")

        clock_equal = self._commit_predicate(raw_phase=payload.phase, equal=True)
        clock_not_equal = self._commit_predicate(raw_phase=payload.phase, equal=False)
        ready_true = self._ready_predicate(ready=True)
        source = self.net_builders[payload.net]

        update = DeciderCombinator(
            id=self._take_entity_id(),
            comparator=clock_equal.comparator,
            left=clock_equal.left,
            right=clock_equal.right,
            output_signal=SIGNAL_EVERYTHING,
            output_copy_count_from_input=True,
            copy_count_nets=(payload.net,),
            additional_conditions=(ready_true,),
            description="Mapped Level HOLD: capture coherent framebuffer",
        )
        self.circuit.entities.append(update)
        update_input = Endpoint(update.id, Connector.INPUT)
        for net in dict.fromkeys((payload.net, self.commit_clock_net, self.commit_ready_net)):
            self._attach(net, update_input)
        self._add_net_conflict(
            payload.net,
            self.commit_clock_net,
            "mapped framebuffer payload and periodic clock require separate networks",
        )
        self._add_net_conflict(
            payload.net,
            self.commit_ready_net,
            "mapped framebuffer payload and startup-ready require separate networks",
        )

        memory_net = self._new_net(
            source.signals,
            Endpoint(update.id, Connector.OUTPUT),
            label="Mapped Level HOLD framebuffer memory",
            fixed_signals=source.fixed_signals,
            carries_dynamic_vector=source.carries_dynamic_vector,
        )
        feedback = DeciderCombinator(
            id=self._take_entity_id(),
            comparator=ready_true.comparator,
            left=ready_true.left,
            right=ready_true.right,
            output_signal=SIGNAL_EVERYTHING,
            output_copy_count_from_input=True,
            copy_count_nets=(memory_net,),
            additional_conditions=(clock_not_equal,),
            description="Mapped Level HOLD: retain framebuffer between boundaries",
        )
        self.circuit.entities.append(feedback)
        feedback_input = Endpoint(feedback.id, Connector.INPUT)
        for net in dict.fromkeys((memory_net, self.commit_clock_net, self.commit_ready_net)):
            self._attach(net, feedback_input)
        self._attach(memory_net, Endpoint(feedback.id, Connector.OUTPUT))
        self._add_net_conflict(
            memory_net,
            self.commit_clock_net,
            "mapped framebuffer memory and periodic clock require separate networks",
        )
        self._add_net_conflict(
            memory_net,
            self.commit_ready_net,
            "mapped framebuffer memory and startup-ready require separate networks",
        )

        self.output_materialization_entities += 2
        return RealizedVector(memory_net, payload.phase + 1)

    def _create_output_markers(
        self,
        outputs: list[RealizedValue | RealizedVector],
    ) -> None:
        materialized: list[RealizedValue | RealizedVector] = []
        for index, (semantic, realized) in enumerate(
            zip(self.module.output.values, outputs, strict=True)
        ):
            declared_name = self.module.output.names[index] if self.module.output.names else None
            name = declared_name or getattr(semantic, "name", None) or f"out{index}"
            if name == "framebuffer":
                if not isinstance(realized, RealizedVector):
                    raise TypeError("mapped framebuffer output must be a vector")
                realized = self._hold_framebuffer(realized)
            materialized.append(realized)

        # The mapped boundary above deliberately owns materialization for this experimental path.
        # Bypass SettlingVectorLowerer's legacy HOLD policy so the shared mapped commit resource is
        # the only physical clock used here.
        VectorLowerer._create_output_markers(self, materialized)


def lower_periodic_state_mapping_plan(
    module: CircuitModule,
    problem: MappingProblem,
    candidates: tuple[ImplementationCandidate, ...],
    state_candidates: tuple[StateCellCandidate, ...],
    plan: RealizationPlan,
) -> PeriodicStatePhysicalLoweringResult:
    """Lower one mapped periodic plan with coherent continuous framebuffer output."""

    lowerer = _BoundarySafeMappedPeriodicStateLowerer(
        module,
        problem,
        candidates,
        state_candidates,
        plan,
    )
    base: _BasePeriodicStatePhysicalLoweringResult = lowerer.lower()
    return PeriodicStatePhysicalLoweringResult(
        circuit=base.circuit,
        fixed_source_entities=base.fixed_source_entities,
        candidate_internal_entities=base.candidate_internal_entities,
        output_materialization_entities=lowerer.output_materialization_entities,
        planned_cost=base.planned_cost,
    )


__all__ = ["PeriodicStatePhysicalLoweringResult", "lower_periodic_state_mapping_plan"]
