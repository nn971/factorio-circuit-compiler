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
    DeciderCondition,
    Endpoint,
    Operand,
)
from factorio_circuit.ir.semantic import BinaryOp, CircuitModule, Compare, Constant, Select, Value
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedValue, RealizedVector
from factorio_circuit.lowering.open_vector import VectorLowerer
from factorio_circuit.target.factorio.signals import SIGNAL_EVERYTHING

from .decider_cover import flatten_decider_condition_cover
from .plan import RealizationPlan
from .problem import MappingProblem, MappingProblemError
from .state_lower import (
    PeriodicStatePhysicalLoweringResult as _BasePeriodicStatePhysicalLoweringResult,
)
from .state_lower import (
    _MappedPeriodicStateLowerer,
)
from .state_templates import StateCellCandidate
from .templates import ImplementationCandidate, ImplementationRecipe


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

    def __init__(
        self,
        module: CircuitModule,
        problem: MappingProblem,
        candidates: tuple[ImplementationCandidate, ...],
        state_candidates: tuple[StateCellCandidate, ...],
        plan: RealizationPlan,
    ) -> None:
        super().__init__(module, problem, candidates, state_candidates, plan)
        self.output_materialization_entities = 0

    def delay_to(self, value: RealizedValue, target_phase: int) -> RealizedValue:
        if value.phase == target_phase:
            return value
        return super().delay_to(value, target_phase)

    def delay_vector_to(self, value: RealizedVector, target_phase: int) -> RealizedVector:
        if value.phase == target_phase:
            return value
        return super().delay_vector_to(value, target_phase)

    def _selected_candidate(self, semantic: object) -> tuple[int, object, ImplementationCandidate] | None:
        operation_id = self.operation_id_by_semantic.get(id(semantic))
        if operation_id is None:
            return None
        realization = self.realization_by_operation[operation_id]
        candidate = self.candidate_by_id[realization.candidate]
        return operation_id, realization, candidate

    def _realize_binary(self, op: BinaryOp) -> RealizedValue:
        selected = self._selected_candidate(op)
        if selected is None:
            return super()._realize_binary(op)
        operation_id, realization, candidate = selected
        if candidate.recipe is ImplementationRecipe.DECIDER_CONDITION_COVER:
            return self._realize_decider_condition_cover(op, operation_id, realization, candidate)
        if candidate.recipe is ImplementationRecipe.COVERED_BY_DECIDER:
            raise MappingProblemError(
                f"covered boolean operation {operation_id} escaped its decider-cover root"
            )
        return super()._realize_binary(op)

    def _realize_compare(self, comparison: Compare) -> RealizedValue:
        selected = self._selected_candidate(comparison)
        if selected is not None and selected[2].recipe is ImplementationRecipe.COVERED_BY_DECIDER:
            raise MappingProblemError(
                f"covered comparison {selected[0]} escaped its decider-cover root"
            )
        return super()._realize_compare(comparison)

    @staticmethod
    def _normalize_compare_values(
        left: Value,
        right: Value,
        op: str,
    ) -> tuple[Value, Value, str]:
        if not isinstance(left, Constant) or isinstance(right, Constant):
            return left, right, op
        swapped = {
            "==": "==",
            "!=": "!=",
            "<": ">",
            "<=": ">=",
            ">": "<",
            ">=": "<=",
        }[op]
        return right, left, swapped

    def _realize_decider_condition_cover(
        self,
        root: BinaryOp,
        operation_id: int,
        realization: object,
        candidate: ImplementationCandidate,
    ) -> RealizedValue:
        flattened = flatten_decider_condition_cover(root)
        if flattened is None:
            raise MappingProblemError("selected decider cover no longer matches its semantic tree")
        boolean_op, comparisons = flattened
        if candidate.entity_cost != 1 or len(set(candidate.input_phase_offsets)) != 1:
            raise MappingProblemError("decider cover candidate metadata is inconsistent")
        output_phase = self.realization_by_operation[operation_id].output_phase
        input_phase = output_phase + candidate.input_phase_offsets[0]

        prepared: list[tuple[str, Operand, Operand]] = []
        dynamic_nets: list[int] = []
        for comparison in comparisons:
            left_value, right_value, comparator = self._normalize_compare_values(
                comparison.left,
                comparison.right,
                comparison.op,
            )
            left = self._realize_operand_value(left_value)
            right = self._realize_operand_value(right_value)
            if isinstance(left, RealizedValue):
                left = self.delay_to(left, input_phase)
                dynamic_nets.append(left.net)
            if isinstance(right, RealizedValue):
                right = self.delay_to(right, input_phase)
                dynamic_nets.append(right.net)
            left_operand, right_operand = self._scalar_operand_layout(left, right)
            prepared.append((comparator, left_operand, right_operand))

        if not prepared:  # pragma: no cover - cover finder requires at least two leaves
            raise AssertionError("empty decider condition cover")
        primary_comparator, primary_left, primary_right = prepared[0]
        compare_type = "or" if boolean_op == "|" else "and"
        additional = tuple(
            DeciderCondition(
                comparator=comparator,
                left=left,
                right=right,
                compare_type=compare_type,
            )
            for comparator, left, right in prepared[1:]
        )
        output_signal = self._new_signal(root.name or f"decider cover {operation_id}")
        entity = DeciderCombinator(
            id=self._take_entity_id(),
            comparator=primary_comparator,
            left=primary_left,
            right=primary_right,
            output_signal=output_signal,
            output_constant=1,
            additional_conditions=additional,
            description=(
                f"Mapped Decider cover op {operation_id}: "
                f"{len(comparisons)}-condition {'OR' if boolean_op == '|' else 'AND'}"
            ),
        )
        self.circuit.entities.append(entity)
        endpoint = Endpoint(entity.id, Connector.INPUT)
        for net in dict.fromkeys(dynamic_nets):
            self._attach(net, endpoint)
        output_net = self._new_net(
            (output_signal,),
            Endpoint(entity.id, Connector.OUTPUT),
            label=root.name or f"decider cover {operation_id}",
        )
        result = RealizedValue(output_signal, output_net, output_phase)
        if input_phase + 1 != output_phase:
            raise MappingProblemError("decider cover must have exactly one tick of target latency")
        return result

    def _realize_select(self, select: Select) -> RealizedValue:
        """Lower the exact Select recipe chosen by the temporal mapper."""

        operation_id = self.operation_id_by_semantic.get(id(select))
        if operation_id is None:
            return super()._realize_select(select)
        realization = self.realization_by_operation[operation_id]
        candidate = self.candidate_by_id[realization.candidate]
        if candidate.recipe is ImplementationRecipe.ORDINARY:
            return super()._realize_select(select)

        if not isinstance(select.when_true, Constant) or not isinstance(
            select.when_false, Constant
        ):
            raise MappingProblemError(
                "specialized mapped Select recipe requires compile-time constant arms"
            )
        condition_phase = realization.output_phase + candidate.input_phase_offsets[0]
        condition = self.delay_to(self.realize(select.condition), condition_phase)
        true_value = select.when_true.value
        false_value = select.when_false.value
        label = select.name or f"op {operation_id}"

        if candidate.recipe is ImplementationRecipe.SELECT_CONSTANT_ZERO_FALSE:
            if false_value != 0 or candidate.entity_cost != 1:
                raise MappingProblemError("invalid zero-false Select candidate metadata")
            result = self._emit_binary_from_operands(
                "*",
                condition,
                true_value,
                description=f"Mapped Select {label}: constant gate ({true_value} when true)",
            )
        elif candidate.recipe is ImplementationRecipe.SELECT_CONSTANT_FOLDED:
            if false_value == 0 or candidate.entity_cost != 2:
                raise MappingProblemError("invalid folded-constant Select candidate metadata")
            delta = true_value - false_value
            gated = self._emit_binary_from_operands(
                "*",
                condition,
                delta,
                description=f"Mapped Select {label}: folded constant delta {delta}",
            )
            result = self._emit_binary_from_operands(
                "+",
                gated,
                false_value,
                description=f"Mapped Select {label}: add false constant {false_value}",
            )
        else:  # pragma: no cover - enum exhaustiveness guard
            raise MappingProblemError(
                f"unsupported mapped Select recipe {candidate.recipe.value!r}"
            )

        if result.phase != realization.output_phase:
            raise MappingProblemError(
                f"specialized mapped Select realized at phase {result.phase}, "
                f"expected {realization.output_phase}"
            )
        return result

    def _hold_framebuffer(self, payload: RealizedVector) -> RealizedVector:
        """Capture one coherent framebuffer once per mapped period and hold it continuously."""

        if self.commit_clock_net is None or self.commit_ready_net is None:
            raise AssertionError(
                "periodic commit resource must exist before output materialization"
            )

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
    base: _BasePeriodicStatePhysicalLoweringResult = lowerer.lower_mapped()
    return PeriodicStatePhysicalLoweringResult(
        circuit=base.circuit,
        fixed_source_entities=base.fixed_source_entities,
        candidate_internal_entities=base.candidate_internal_entities,
        output_materialization_entities=lowerer.output_materialization_entities,
        planned_cost=base.planned_cost,
    )


__all__ = ["PeriodicStatePhysicalLoweringResult", "lower_periodic_state_mapping_plan"]
