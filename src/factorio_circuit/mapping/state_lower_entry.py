"""Public entry point for periodic-state physical lowering.

The mature lowering stack may ask to align an operand to a phase it already occupies. Those calls
are semantically no-ops and must not consume a second planned delivery from the temporal mapping
plan. The mapped backend also has to honor the dense Level output boundary: a display observes its
network continuously, so a coherent framebuffer sampled at one phase must be held while the next
logical occurrence settles internally.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

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
from .plan import DeliveryKind, RealizationPlan, WireSumResource
from .problem import MappingProblem, MappingProblemError, MappingUse
from .state_lower import (
    PeriodicStatePhysicalLoweringResult as _BasePeriodicStatePhysicalLoweringResult,
)
from .state_lower import (
    _MappedPeriodicStateLowerer,
)
from .state_templates import StateCellCandidate
from .templates import ImplementationCandidate, ImplementationKind, ImplementationRecipe


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
        # The mature base lowerer predates periodic wire sums and deliberately rejects the resource.
        # Validate/build all of its existing transport/state machinery against an equivalent shadow,
        # then restore the real candidate/resource metadata for the subclass-owned zero-cost
        # lowering.
        base_candidates = tuple(
            replace(candidate, kind=ImplementationKind.ORDINARY)
            if candidate.kind is ImplementationKind.WIRE_SUM
            else candidate
            for candidate in candidates
        )
        base_plan = replace(plan, wire_sums=())
        super().__init__(module, problem, base_candidates, state_candidates, base_plan)
        self.plan = plan
        self.candidate_by_id = {item.id: item for item in candidates}
        self.output_materialization_entities = 0

        self.wire_sum_by_operation = {item.operation: item for item in plan.wire_sums}
        self.wire_sum_target_by_producer: dict[int, WireSumResource] = {}
        for resource in plan.wire_sums:
            for producer in (resource.left_producer, resource.right_producer):
                previous = self.wire_sum_target_by_producer.setdefault(producer, resource)
                if previous != resource:
                    raise MappingProblemError(
                        "one periodic physical realization cannot contribute to two wire sums"
                    )
        self.wire_sum_carrier: dict[int, RealizedValue] = {}

    def realize(self, value: Value) -> RealizedValue:
        result = super().realize(value)
        operation_id = self.operation_id_by_semantic.get(id(value))
        if operation_id is None:
            return result
        resource = self.wire_sum_target_by_producer.get(operation_id)
        if resource is None:
            return result
        rebound = self._bind_wire_sum_contributor(operation_id, resource, result)
        self.memo[id(value)] = rebound
        self.scalar_origin.pop((result.net, result.signal), None)
        self.scalar_origin[(rebound.net, rebound.signal)] = operation_id
        return rebound

    def _bind_wire_sum_contributor(
        self,
        operation_id: int,
        resource: WireSumResource,
        value: RealizedValue,
    ) -> RealizedValue:
        if value.phase != resource.phase:
            raise MappingProblemError("periodic wire-sum contribution lowered at the wrong phase")
        if not value.clean_single_lane or not isinstance(value.signal, int):
            raise MappingProblemError(
                "periodic wire-sum contribution requires a clean abstract scalar output lane"
            )

        carrier = self.wire_sum_carrier.get(resource.operation)
        if carrier is None:
            self.wire_sum_carrier[resource.operation] = value
            return value
        if carrier.phase != resource.phase or not isinstance(carrier.signal, int):
            raise MappingProblemError("periodic wire-sum carrier metadata is inconsistent")
        if (value.net, value.signal) == (carrier.net, carrier.signal):
            return carrier

        old_net = self.net_builders[value.net]
        if old_net.fixed_signals or old_net.carries_dynamic_vector:
            raise MappingProblemError("periodic wire-sum contributor is not an isolated scalar net")
        if not old_net.endpoints or any(
            endpoint.connector is not Connector.OUTPUT for endpoint in old_net.endpoints
        ):
            raise MappingProblemError(
                "periodic wire-sum contributor output was observed before carrier aggregation"
            )
        if any(
            value.net in (conflict.left, conflict.right) for conflict in self.circuit.net_conflicts
        ):
            raise MappingProblemError(
                "periodic wire-sum contributor output already participates in a net conflict"
            )
        if any(
            value.signal in (conflict.left, conflict.right)
            for conflict in self.circuit.signal_conflicts
        ):
            raise MappingProblemError(
                "periodic wire-sum contributor output already participates in a signal conflict"
            )

        self._add_signal_alias(
            carrier.signal,
            value.signal,
            f"periodic wire sum {resource.operation}: contributors share one Factorio signal",
        )
        for endpoint in old_net.endpoints:
            self._attach(carrier.net, endpoint)
        del self.net_builders[value.net]
        return RealizedValue(
            carrier.signal,
            carrier.net,
            resource.phase,
            clean_single_lane=True,
        )

    def delay_to(self, value: RealizedValue, target_phase: int) -> RealizedValue:
        if value.phase == target_phase:
            return value
        return super().delay_to(value, target_phase)

    def delay_vector_to(self, value: RealizedVector, target_phase: int) -> RealizedVector:
        if value.phase == target_phase:
            return value
        return super().delay_vector_to(value, target_phase)

    def _selected_candidate(self, semantic: object) -> tuple[int, ImplementationCandidate] | None:
        operation_id = self.operation_id_by_semantic.get(id(semantic))
        if operation_id is None:
            return None
        realization = self.realization_by_operation[operation_id]
        candidate = self.candidate_by_id[realization.candidate]
        return operation_id, candidate

    def _realize_binary(self, op: BinaryOp) -> RealizedValue:
        selected = self._selected_candidate(op)
        if selected is None:
            return super()._realize_binary(op)
        operation_id, candidate = selected
        if candidate.kind is ImplementationKind.WIRE_SUM:
            return self._realize_wire_sum(op, operation_id, candidate)
        if candidate.recipe is ImplementationRecipe.DECIDER_CONDITION_COVER:
            return self._realize_decider_condition_cover(op, operation_id, candidate)
        if candidate.recipe is ImplementationRecipe.COVERED_BY_DECIDER:
            raise MappingProblemError(
                f"covered boolean operation {operation_id} escaped its decider-cover root"
            )
        return super()._realize_binary(op)

    def _realize_wire_sum(
        self,
        operation: BinaryOp,
        operation_id: int,
        candidate: ImplementationCandidate,
    ) -> RealizedValue:
        if operation.op != "+" or candidate.entity_cost != 0:
            raise MappingProblemError("selected periodic wire-sum candidate metadata is invalid")
        resource = self.wire_sum_by_operation.get(operation_id)
        if resource is None:
            raise MappingProblemError("selected periodic wire sum has no plan resource")
        mapping_operation = self.problem.operation_by_id(operation_id)
        if tuple(mapping_operation.operands) != (
            resource.left_producer,
            resource.right_producer,
        ):
            raise MappingProblemError("periodic wire-sum plan resource has the wrong contributors")

        left = self.realize(operation.left)
        right = self.realize(operation.right)
        for operand_index, (producer, realized) in enumerate(
            zip(mapping_operation.operands, (left, right), strict=True)
        ):
            claimed = self._claim_delivery(producer, resource.phase)
            if claimed is None:
                raise MappingProblemError("periodic wire-sum contribution has no planned delivery")
            use, delivery = claimed
            expected_use = MappingUse(producer, operation_id, operand_index)
            if use != expected_use:
                raise MappingProblemError("periodic wire-sum claimed the wrong semantic delivery")
            if (
                delivery.kind is not DeliveryKind.REUSE
                or delivery.transport_start_phase is not None
                or delivery.phase != resource.phase
            ):
                raise MappingProblemError(
                    "periodic wire-sum contribution must be a same-phase free delivery"
                )
            if realized.phase != resource.phase:
                raise MappingProblemError("periodic wire-sum contributor has the wrong phase")

        carrier = self.wire_sum_carrier.get(operation_id)
        if carrier is None:
            raise MappingProblemError("periodic wire-sum contributors did not establish a carrier")
        if not (
            left.signal == right.signal == carrier.signal
            and left.net == right.net == carrier.net
            and left.phase == right.phase == carrier.phase == resource.phase
        ):
            raise MappingProblemError("periodic wire-sum contributors did not share one carrier")
        return carrier

    def _realize_compare(self, comparison: Compare) -> RealizedValue:
        selected = self._selected_candidate(comparison)
        if selected is not None and selected[1].recipe is ImplementationRecipe.COVERED_BY_DECIDER:
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
