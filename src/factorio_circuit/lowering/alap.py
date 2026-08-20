"""As-late-as-possible scheduling for periodic Level computation.

The state timing analyzer chooses a physical transition-input phase for every periodic register.
Historically physical lowering then realized the entire expression as soon as its operands were
available and padded the early result to that boundary.  For a fanout such as ``f(x), g(x), ...``
this can duplicate long delay chains after the fanout.

This module instead propagates state-boundary deadlines backwards through the semantic DAG.  Each
ordinary operation is emitted as late as its earliest consumer permits.  Leaves are *not*
re-sampled:
when an external Level snapshot must survive until the chosen operation phase, the ordinary exact
delay machinery transports that snapshot.  Scalar/vector delay caches can therefore share the
transport prefix before cheap computations branch.

The schedule is deliberately conservative:

* only periodic state-transition cones receive deadlines;
* shared values use the earliest deadline of all consumers;
* scalar ``Select`` uses the generic three-stage data-path envelope from ``TargetLatencyModel``;
* missing deadlines retain the previous ASAP behavior;
* packing may keep its existing schedule when a packed implementation is selected.
"""

from __future__ import annotations

from dataclasses import dataclass

from factorio_circuit.analysis.latency import FACTORIO_LATENCY
from factorio_circuit.analysis.state_timing import StateTimingPlan
from factorio_circuit.ir.abstract_physical import (
    Connector,
    DeciderCombinator,
    Endpoint,
)
from factorio_circuit.ir.semantic import (
    BinaryOp,
    CircuitModule,
    Compare,
    Select,
    Value,
    VectorBinaryOp,
    VectorFilter,
    VectorScalarOp,
    VectorSelect,
    VectorSignal,
)
from factorio_circuit.ir.state import state_transitions
from factorio_circuit.lowering.ir_to_abstract_physical import (
    RealizedValue,
    _normalize_compare,
)
from factorio_circuit.lowering.vector_delay_trunks import SharedVectorDelayLowerer


@dataclass(frozen=True, slots=True)
class AlapSchedule:
    """Latest allowed output phase for semantic nodes in periodic state cones."""

    output_phases: dict[int, int]

    def phase_for(self, value: object) -> int | None:
        return self.output_phases.get(id(value))


def build_alap_schedule(module: CircuitModule, timing: StateTimingPlan) -> AlapSchedule:
    """Propagate periodic state-boundary deadlines backwards through the semantic DAG."""

    deadlines: dict[int, int] = {}

    def demand(value: object | None, phase: int) -> None:
        if value is None:
            return
        key = id(value)
        previous = deadlines.get(key)
        if previous is not None and previous <= phase:
            return
        deadlines[key] = phase

        if isinstance(value, (BinaryOp, Compare)):
            child_phase = phase - FACTORIO_LATENCY.operation_latency(
                "scalar_binary" if isinstance(value, BinaryOp) else "compare",
                value.op,
            )
            demand(value.left, child_phase)
            demand(value.right, child_phase)
            return

        if isinstance(value, Select):
            # The physical lowerer may use a one-stage decider mux, but the generic fallback is
            # false + (true - false) * condition.  Scheduling all three semantic children at the
            # conservative data-path deadline guarantees that either realization still meets the
            # requested output phase.
            child_phase = phase - FACTORIO_LATENCY.operation_latency("select_data", value.name)
            demand(value.condition, child_phase)
            demand(value.when_true, child_phase)
            demand(value.when_false, child_phase)
            return

        if isinstance(value, VectorSignal):
            demand(value.vector, phase)
            return

        if isinstance(value, VectorBinaryOp):
            child_phase = phase - FACTORIO_LATENCY.operation_latency("vector_binary", value.op)
            demand(value.left, child_phase)
            demand(value.right, child_phase)
            return

        if isinstance(value, VectorScalarOp):
            child_phase = phase - FACTORIO_LATENCY.operation_latency("vector_scalar", value.op)
            demand(value.vector, child_phase)
            demand(value.scalar, child_phase)
            return

        if isinstance(value, VectorFilter):
            family = "vector_select" if isinstance(value, VectorSelect) else "vector_filter"
            child_phase = phase - FACTORIO_LATENCY.operation_latency(family, value.op)
            demand(value.vector, child_phase)
            return

        # Inputs, samples, constants, and state reads are leaves.  Their requested phase remains in
        # the map for diagnostics, while physical lowering retains their existing snapshot
        # semantics.

    control_latency = FACTORIO_LATENCY.state_transition_latency("commit")
    for transition in state_transitions(module):
        if transition.trigger is not None:
            continue
        register_timing = timing.for_register(transition.register)
        target = register_timing.transition_input_phase
        if transition.value is not None:
            demand(transition.value, target)
        if transition.when is not None:
            # The periodic state lowerer normalizes the semantic condition through one final
            # decider/commit-control stage before the transition input phase.
            demand(transition.when, target - control_latency)

    return AlapSchedule(deadlines)


class AlapVectorLowerer(SharedVectorDelayLowerer):
    """Validity-aware production lowerer with backward ALAP operation placement."""

    def __init__(
        self,
        module: CircuitModule,
        *,
        enable_packing: bool,
        state_timing: StateTimingPlan | None = None,
    ) -> None:
        super().__init__(
            module,
            enable_packing=enable_packing,
            state_timing=state_timing,
        )
        self.alap_schedule = build_alap_schedule(module, self.state_timing)

    def _operation_input_phase(
        self,
        semantic: object,
        family: str,
        operation: str | None,
        default_phase: int,
    ) -> int:
        """Return the latest scheduled input phase without ever moving an operation earlier."""

        output_phase = self.alap_schedule.phase_for(semantic)
        if output_phase is None:
            return default_phase
        requested = output_phase - FACTORIO_LATENCY.operation_latency(family, operation)
        return max(default_phase, requested)

    def realize(self, value: Value) -> RealizedValue:
        if not isinstance(value, VectorSignal):
            return super().realize(value)

        cached = self.memo.get(id(value))
        if cached is not None:
            return cached
        vector = self.realize_vector(value.vector)
        requested = self.alap_schedule.phase_for(value)
        if requested is not None and requested > vector.phase:
            # A lane read is a zero-latency view.  Transport the complete vector snapshot before
            # projecting the lane so different lane consumers share one exact vector-delay trunk.
            vector = self.delay_vector_to(vector, requested)
        result = RealizedValue(
            value.signal,
            vector.net,
            vector.phase,
            clean_single_lane=False,
        )
        self.memo[id(value)] = result
        self._record_scalar_semantics(value, result)
        return result

    def _realize_binary(self, op: BinaryOp) -> RealizedValue:
        # Keep existing physical packing semantics.  Unpacked operations, including the canonical
        # Snake benchmark path, receive the ALAP schedule below.
        partition = self.partition_for_op.get(id(op))
        if partition is not None and self._try_emit_partition(partition):
            return self.memo[id(op)]
        pairwise = self.pairwise_partition_for_op.get(id(op))
        if pairwise is not None and self._try_emit_pairwise_partition(pairwise, op):
            return self.memo[id(op)]

        left = self._realize_operand_value(op.left)
        right = self._realize_operand_value(op.right)
        base = max(
            (item.phase for item in (left, right) if isinstance(item, RealizedValue)),
            default=0,
        )
        target = self._operation_input_phase(op, "scalar_binary", op.op, base)
        if isinstance(left, RealizedValue):
            left = self.delay_to(left, target)
        if isinstance(right, RealizedValue):
            right = self.delay_to(right, target)
        return self._emit_binary_from_operands(op.op, left, right, description=op.name)

    def _realize_compare(self, comparison: Compare) -> RealizedValue:
        left_value, right_value, comparator = _normalize_compare(
            comparison.left,
            comparison.right,
            comparison.op,
        )
        left = self._realize_operand_value(left_value)
        right = self._realize_operand_value(right_value)
        base = max(
            (item.phase for item in (left, right) if isinstance(item, RealizedValue)),
            default=0,
        )
        target = self._operation_input_phase(comparison, "compare", comparison.op, base)
        if isinstance(left, RealizedValue):
            left = self.delay_to(left, target)
        if isinstance(right, RealizedValue):
            right = self.delay_to(right, target)

        out = self._new_signal(comparison.name or "compare")
        left_operand, right_operand = self._scalar_operand_layout(left, right)
        entity = DeciderCombinator(
            id=self._take_entity_id(),
            comparator=comparator,
            left=left_operand,
            right=right_operand,
            output_signal=out,
            output_constant=1,
            description=comparison.name,
        )
        self.circuit.entities.append(entity)
        self._attach_dynamic_inputs(left, right, Endpoint(entity.id, Connector.INPUT))
        net = self._new_net(
            (out,),
            Endpoint(entity.id, Connector.OUTPUT),
            label=comparison.name or "compare",
        )
        return RealizedValue(
            out,
            net,
            target + FACTORIO_LATENCY.operation_latency("compare", comparison.op),
        )

    def _realize_select(self, select: Select) -> RealizedValue:
        if self.enable_packing and isinstance(select.condition, Compare):
            fused = self._try_emit_shared_compare_selects(select)
            if fused is not None:
                return fused
            inlined = self._try_emit_inline_compare_select(select)
            if inlined is not None:
                return inlined

        condition = self.realize(select.condition)
        when_true = self._realize_operand_value(select.when_true)
        when_false = self._realize_operand_value(select.when_false)
        base = max(
            [
                condition.phase,
                *(
                    item.phase
                    for item in (when_true, when_false)
                    if isinstance(item, RealizedValue)
                ),
            ]
        )
        target = self._operation_input_phase(select, "select_data", select.name, base)
        condition = self.delay_to(condition, target)
        if isinstance(when_true, RealizedValue):
            when_true = self.delay_to(when_true, target)
        if isinstance(when_false, RealizedValue):
            when_false = self.delay_to(when_false, target)

        if not (
            self._can_use_decider_mux_arm(condition, when_true)
            and self._can_use_decider_mux_arm(condition, when_false)
        ):
            diff = self._emit_binary_from_operands("-", when_true, when_false)
            gated = self._emit_binary_from_realized("*", diff, condition)
            return self._emit_binary_from_operands("+", when_false, gated, description=select.name)

        out = self._new_signal(select.name or "select")
        true_arm = self._emit_decider_mux_arm(
            condition,
            when_true,
            output_signal=out,
            active_when_true=True,
            description=f"{select.name or 'select'}: true arm",
        )
        false_arm = self._emit_decider_mux_arm(
            condition,
            when_false,
            output_signal=out,
            active_when_true=False,
            description=f"{select.name or 'select'}: false arm",
        )
        output_net = self._new_net(
            (out,),
            Endpoint(true_arm.id, Connector.OUTPUT),
            label=select.name or "select mux",
        )
        self._attach(output_net, Endpoint(false_arm.id, Connector.OUTPUT))
        return RealizedValue(
            out,
            output_net,
            target + FACTORIO_LATENCY.operation_latency("scalar_binary", "select"),
        )


__all__ = ["AlapSchedule", "AlapVectorLowerer", "build_alap_schedule"]
