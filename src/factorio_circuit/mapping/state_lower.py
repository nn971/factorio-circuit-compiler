"""Lower a validated periodic state mapping plan to Abstract Physical IR.

This path is deliberately separate from production ``compile_circuit``. Timing is derived strictly
from the selected mapping plan: the compatibility ``StateTimingPlan`` constructed here is a backend
adapter for the mature vector/read machinery, never the result of state-timing analysis.

The lowerer materializes:

* ordinary scalar/vector operation candidates at their selected phases;
* the shared three-entity periodic commit resource;
* four-entity clocked Freeze and one-add/one-clear Accumulator cells;
* prefix-shared private scalar/vector exact transport;
* selected isolated scalar delay buses.

Fixed semantic constant sources are reported separately because ``MappingProblem`` does not yet
price them. The ordinary arithmetic Select candidate also has an internal exact false-arm
preservation path
that is intentionally reported as a candidate-internal surcharge until the candidate model grows
explicit internal ports.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from typing import cast

from factorio_circuit.analysis.latency import FACTORIO_LATENCY
from factorio_circuit.analysis.state_timing import (
    ClockDomainTiming,
    RegisterTiming,
    StateReadTiming,
    StateTimingPlan,
)
from factorio_circuit.ir.abstract_physical import (
    AbstractNet,
    AbstractPhysicalCircuit,
    ArithmeticCombinator,
    Connector,
    ConstantCombinator,
    DeciderCombinator,
    DeciderCondition,
    Endpoint,
    Operand,
)
from factorio_circuit.ir.semantic import (
    CircuitModule,
    Constant,
    Select,
    Value,
    VectorConstant,
    VectorValue,
    is_vector_value,
)
from factorio_circuit.ir.state import AccumulatorRegister, FreezeRegister
from factorio_circuit.lowering.alap import AlapSchedule
from factorio_circuit.lowering.input_sampling import SamplingPolicyLowerer
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedValue, RealizedVector
from factorio_circuit.sampling import SamplingPolicy

from .plan import DelayBusLane, DelayBusResource, DeliveryKind, PlannedDelivery, RealizationPlan
from .problem import MappingProblem, MappingProblemError, MappingUse
from .state_templates import StateCellCandidate
from .state_validate import validate_periodic_state_bus_plan
from .templates import ImplementationCandidate

type _BusBinding = tuple[DelayBusResource, DelayBusLane]
type _QueuedDelivery = tuple[MappingUse, PlannedDelivery]


@dataclass(frozen=True, slots=True)
class PeriodicStatePhysicalLoweringResult:
    """Abstract physical result plus costs outside the current mapper objective."""

    circuit: AbstractPhysicalCircuit
    fixed_source_entities: int
    candidate_internal_entities: int
    planned_cost: int

    @property
    def emitted_combinators(self) -> int:
        return self.circuit.combinator_count

    @property
    def accounted_cost(self) -> int:
        return self.planned_cost + self.fixed_source_entities + self.candidate_internal_entities

    @property
    def unexplained_cost_gap(self) -> int:
        return self.emitted_combinators - self.accounted_cost

    @property
    def cost_exact_after_known_surcharges(self) -> bool:
        return self.unexplained_cost_gap == 0


def _backend_state_timing(
    module: CircuitModule,
    problem: MappingProblem,
    plan: RealizationPlan,
) -> StateTimingPlan:
    """Encode selected state phases for the physical backend without running timing analysis."""

    period = problem.period
    if period is None:
        raise MappingProblemError("mapped periodic lowering requires a prescribed period")
    cells = {item.register_name: item for item in plan.state_cells}
    if set(cells) != {item.name for item in module.state_registers}:
        raise MappingProblemError("mapped state cells must cover every module register")

    domain = ClockDomainTiming(
        id=1,
        period=period,
        registers=tuple(module.state_registers),
    )
    timings: list[RegisterTiming] = []
    for register in module.state_registers:
        cell = cells[register.name]
        transitions = tuple(
            item for item in problem.state_transitions if item.register_name == register.name
        )
        if not transitions:
            raise MappingProblemError(f"state register {register.name!r} has no mapped transition")
        if {item.logical_offset for item in transitions} != {0}:
            raise MappingProblemError(
                "first mapped physical state lowering requires offset-zero transitions"
            )
        reads = tuple(
            StateReadTiming(
                read=item.semantic,
                physical_phase=cell.base_read_phase + item.logical_offset * period,
            )
            for item in problem.state_reads
            if item.register_name == register.name
        )
        orders = tuple(item.semantic.order for item in transitions)
        timings.append(
            RegisterTiming(
                register=register,
                clock_domain=domain.id,
                period=period,
                commit_offset=0,
                state_phase=cell.base_read_phase,
                transition_input_phase=cell.base_read_phase + period - 1,
                earliest_transition_input_phase=cell.base_read_phase + period - 1,
                first_update_order=min(orders),
                last_update_order=max(orders),
                reads=reads,
            )
        )
    return StateTimingPlan(domains=(domain,), registers=tuple(timings))


class _MappedPeriodicStateLowerer(SamplingPolicyLowerer):
    def __init__(
        self,
        module: CircuitModule,
        problem: MappingProblem,
        candidates: tuple[ImplementationCandidate, ...],
        state_candidates: tuple[StateCellCandidate, ...],
        plan: RealizationPlan,
    ) -> None:
        validate_periodic_state_bus_plan(problem, candidates, state_candidates, plan)
        if problem.period is None:
            raise MappingProblemError("mapped periodic lowering requires a prescribed period")
        if plan.periodic_commit is None:
            raise MappingProblemError("mapped periodic lowering requires a commit resource")
        if plan.wire_sums:
            raise MappingProblemError(
                "first mapped periodic state lowering does not admit wire sums"
            )

        super().__init__(
            module,
            enable_packing=False,
            state_timing=_backend_state_timing(module, problem, plan),
            sampling_policy=SamplingPolicy.ALAP,
        )
        self.problem = problem
        self.plan = plan
        self.candidate_by_id = {item.id: item for item in candidates}
        self.state_candidate_by_id = {item.id: item for item in state_candidates}
        self.realization_by_operation = {item.operation: item for item in plan.realizations}
        self.value_id_by_semantic = {
            **{id(item.semantic): item.id for item in problem.sources},
            **{id(item.semantic): item.id for item in problem.state_reads},
            **{id(item.semantic): item.id for item in problem.operations},
        }
        self.operation_id_by_semantic = {id(item.semantic): item.id for item in problem.operations}
        self.alap_schedule = AlapSchedule(
            {
                id(problem.operation_by_id(item.operation).semantic): item.output_phase
                for item in plan.realizations
            }
        )

        self.delivery_queues: dict[tuple[int, int], list[_QueuedDelivery]] = defaultdict(list)
        for delivery in plan.deliveries:
            use = MappingUse(delivery.producer, delivery.consumer, delivery.operand_index)
            self.delivery_queues[(delivery.producer, delivery.phase)].append((use, delivery))
        for queue in self.delivery_queues.values():
            queue.sort(
                key=lambda item: (
                    item[0].consumer,
                    -1 if item[0].operand_index is None else item[0].operand_index,
                )
            )

        self.scalar_origin: dict[tuple[int, object], int] = {}
        self.vector_origin: dict[int, int] = {}
        self.fixed_source_entities = 0
        self.candidate_internal_entities = 0

        self.bus_binding_by_producer: dict[int, _BusBinding] = {}
        for bus in plan.delay_buses:
            for lane in bus.lanes:
                previous = self.bus_binding_by_producer.setdefault(lane.producer, (bus, lane))
                if previous != (bus, lane):
                    raise MappingProblemError("one producer cannot occupy two mapped delay buses")
        self.bus_ingress_by_lane: dict[tuple[int, int], RealizedValue] = {}
        self.bus_ingress_net_by_phase: dict[tuple[int, int], int] = {}
        self.bus_short_branch_by_use: dict[MappingUse, RealizedValue] = {}
        self.bus_egress_by_use: dict[MappingUse, RealizedValue] = {}
        self.bus_joins: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
        self.joined_bus_lanes: set[tuple[int, int]] = set()
        self.bus_stage_entity_index: dict[tuple[int, int], int] = {}
        self.bus_stage_output_net: dict[tuple[int, int], int] = {}

        self.commit_clock_signal: int | None = None
        self.commit_clock_net: int | None = None
        self.commit_ready_signal: int | None = None
        self.commit_ready_net: int | None = None

    def lower_mapped(self) -> PeriodicStatePhysicalLoweringResult:
        self._create_input_markers()
        self._emit_periodic_commit_resource()
        self._reserve_state_outputs()
        self._create_state_components()

        realized_outputs: list[RealizedValue | RealizedVector] = []
        for semantic, sink in zip(self.module.output.values, self.problem.sinks, strict=True):
            value_id = self.value_id_by_semantic.get(id(semantic))
            if value_id != sink.value:
                raise MappingProblemError("module output does not match mapped sink value")
            if is_vector_value(semantic):
                vector_base = self.realize_vector(cast(VectorValue, semantic))
                realized_outputs.append(self.delay_vector_to(vector_base, sink.phase))
            else:
                scalar_base = self.realize(cast(Value, semantic))
                realized_outputs.append(self.delay_to(scalar_base, sink.phase))

        self._create_output_markers(realized_outputs)

        for bus in self.plan.delay_buses:
            for phase in range(bus.middle_start_phase, bus.middle_end_phase):
                self._ensure_bus_stage(bus, phase)

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
        return PeriodicStatePhysicalLoweringResult(
            circuit=self.circuit,
            fixed_source_entities=self.fixed_source_entities,
            candidate_internal_entities=self.candidate_internal_entities,
            planned_cost=self.plan.total_cost,
        )

    def realize(self, value: Value) -> RealizedValue:
        was_cached = id(value) in self.memo
        before = self.circuit.combinator_count
        result = super().realize(value)
        value_id = self.value_id_by_semantic.get(id(value))
        if value_id is not None:
            self.scalar_origin[(result.net, result.signal)] = value_id
            operation_id = self.operation_id_by_semantic.get(id(value))
            if operation_id is not None:
                expected = self.realization_by_operation[operation_id].output_phase
                if result.phase != expected:
                    raise MappingProblemError(
                        f"mapped operation {operation_id} lowered at phase {result.phase}, "
                        f"expected {expected}"
                    )
        if not was_cached and isinstance(value, Constant) and value_id is not None:
            self.fixed_source_entities += self.circuit.combinator_count - before
        return result

    def realize_vector(self, value: VectorValue) -> RealizedVector:
        was_cached = id(value) in self.vector_memo
        before = self.circuit.combinator_count
        result = super().realize_vector(value)
        value_id = self.value_id_by_semantic.get(id(value))
        if value_id is not None:
            self.vector_origin[result.net] = value_id
            operation_id = self.operation_id_by_semantic.get(id(value))
            if operation_id is not None:
                expected = self.realization_by_operation[operation_id].output_phase
                if result.phase != expected:
                    raise MappingProblemError(
                        f"mapped vector operation {operation_id} lowered at phase {result.phase}, "
                        f"expected {expected}"
                    )
        if not was_cached and isinstance(value, VectorConstant) and value_id is not None:
            self.fixed_source_entities += self.circuit.combinator_count - before
        return result

    def delay_to(self, value: RealizedValue, target_phase: int) -> RealizedValue:
        producer = self.scalar_origin.get((value.net, value.signal))
        claimed = self._claim_delivery(producer, target_phase)
        if claimed is None:
            return super().delay_to(value, target_phase)
        use, delivery = claimed
        result = self._lower_scalar_delivery(use, delivery, value)
        self.scalar_origin[(result.net, result.signal)] = delivery.producer
        return result

    def delay_vector_to(self, value: RealizedVector, target_phase: int) -> RealizedVector:
        producer = self.vector_origin.get(value.net)
        claimed = self._claim_delivery(producer, target_phase)
        if claimed is None:
            return super().delay_vector_to(value, target_phase)
        _use, delivery = claimed
        if delivery.kind in {DeliveryKind.REUSE, DeliveryKind.OBSERVE_AT}:
            before = self.circuit.combinator_count
            result = super().delay_vector_to(value, target_phase)
            if self.circuit.combinator_count != before:
                raise MappingProblemError("free mapped vector delivery materialized transport")
        elif delivery.kind is DeliveryKind.PRIVATE_TRANSPORT:
            if delivery.transport_start_phase is None:
                raise MappingProblemError("private vector transport has no start phase")
            if value.phase > delivery.transport_start_phase:
                raise MappingProblemError("vector producer appears after planned transport anchor")
            anchored = RealizedVector(value.net, delivery.transport_start_phase)
            result = self.exact_delay_vector_to(anchored, target_phase)
        else:
            raise MappingProblemError("shared delay bus cannot carry vector values")
        self.vector_origin[result.net] = delivery.producer
        return result

    def _claim_delivery(
        self,
        producer: int | None,
        target_phase: int,
    ) -> _QueuedDelivery | None:
        if producer is None:
            return None
        queue = self.delivery_queues.get((producer, target_phase))
        if not queue:
            return None
        return queue.pop(0)

    def _lower_scalar_delivery(
        self,
        use: MappingUse,
        delivery: PlannedDelivery,
        value: RealizedValue,
    ) -> RealizedValue:
        if delivery.kind in {DeliveryKind.REUSE, DeliveryKind.OBSERVE_AT}:
            before = self.circuit.combinator_count
            result = super().delay_to(value, delivery.phase)
            if self.circuit.combinator_count != before:
                raise MappingProblemError("free mapped scalar delivery materialized transport")
            return result
        if delivery.transport_start_phase is None:
            raise MappingProblemError("mapped transport delivery has no start phase")
        if delivery.kind is DeliveryKind.PRIVATE_TRANSPORT:
            if value.phase > delivery.transport_start_phase:
                raise MappingProblemError("scalar producer appears after planned transport anchor")
            anchored = RealizedValue(
                value.signal,
                value.net,
                delivery.transport_start_phase,
                value.clean_single_lane,
            )
            return self.exact_delay_to(anchored, delivery.phase)
        if delivery.kind is DeliveryKind.BUS_TRANSPORT:
            return self._delay_on_bus(use, delivery, value)
        raise MappingProblemError(f"unsupported mapped delivery {delivery.kind.value!r}")

    def _realize_select(self, select: Select) -> RealizedValue:
        """Use the exact three-stage arithmetic Select modeled by the mapper."""

        operation_id = self.operation_id_by_semantic.get(id(select))
        if operation_id is None:
            return super()._realize_select(select)
        output_phase = self.realization_by_operation[operation_id].output_phase
        data_phase = output_phase - FACTORIO_LATENCY.operation_latency("select_data", select.name)
        condition_phase = output_phase - FACTORIO_LATENCY.operation_latency(
            "select_condition", select.name
        )

        condition = self.delay_to(self.realize(select.condition), condition_phase)
        when_true = self._realize_operand_value(select.when_true)
        when_false = self._realize_operand_value(select.when_false)
        if isinstance(when_true, RealizedValue):
            when_true = self.delay_to(when_true, data_phase)
        if isinstance(when_false, RealizedValue):
            when_false = self.delay_to(when_false, data_phase)

        diff = self._emit_binary_from_operands("-", when_true, when_false)
        if diff.phase > condition_phase:
            raise MappingProblemError("mapped Select data stage missed its condition boundary")
        if diff.phase < condition_phase:
            diff = super().delay_to(diff, condition_phase)
        gated = self._emit_binary_from_realized("*", diff, condition)
        final_input_phase = output_phase - 1
        if gated.phase != final_input_phase:
            raise MappingProblemError("mapped Select gate stage disagrees with target latency")

        final_false = when_false
        if isinstance(final_false, RealizedValue) and final_false.phase < final_input_phase:
            before = self.circuit.combinator_count
            final_false = self.exact_delay_to(final_false, final_input_phase)
            self.candidate_internal_entities += self.circuit.combinator_count - before
        result = self._emit_binary_from_operands("+", final_false, gated, description=select.name)
        if result.phase != output_phase:
            raise MappingProblemError("mapped Select realized at the wrong output phase")
        return result

    def _emit_periodic_commit_resource(self) -> None:
        resource = self.plan.periodic_commit
        if resource is None:  # pragma: no cover - constructor check
            raise AssertionError("missing periodic commit resource")
        period = resource.period

        clock_signal = self._new_signal("mapped periodic clock")
        source = ConstantCombinator(
            id=self._take_entity_id(),
            signals=((clock_signal, 1),),
            description="mapped periodic commit: +1",
        )
        counter_id = self._take_entity_id()
        clock_net = self._new_net(
            (clock_signal,),
            Endpoint(source.id, Connector.SINGLE),
            label=f"mapped periodic commit: modulo-{period}",
        )
        counter = ArithmeticCombinator(
            id=counter_id,
            operation="%",
            left=Operand(signal=clock_signal, nets=(clock_net,)),
            right=Operand(constant=period),
            output_each=False,
            output_signal=clock_signal,
            description=f"mapped periodic commit: modulo-{period} counter",
        )
        self.circuit.entities.extend((source, counter))
        self._attach(clock_net, Endpoint(counter_id, Connector.INPUT))
        self._attach(clock_net, Endpoint(counter_id, Connector.OUTPUT))

        ready_signal = self._new_signal("mapped periodic ready")
        ready_id = self._take_entity_id()
        ready_net = self._new_net(
            (ready_signal,),
            Endpoint(ready_id, Connector.OUTPUT),
            label="mapped periodic commit: ready latch",
        )
        ready = DeciderCombinator(
            id=ready_id,
            comparator="==",
            left=Operand(signal=clock_signal, nets=(clock_net,)),
            right=Operand(constant=period - 2),
            output_signal=ready_signal,
            output_constant=1,
            additional_conditions=(
                DeciderCondition(
                    comparator="!=",
                    left=Operand(signal=ready_signal, nets=(ready_net,)),
                    right=Operand(constant=0),
                    compare_type="or",
                ),
            ),
            description="mapped periodic commit: ready after first safe boundary",
        )
        self.circuit.entities.append(ready)
        self._attach(clock_net, Endpoint(ready_id, Connector.INPUT))
        self._attach(ready_net, Endpoint(ready_id, Connector.INPUT))

        self.commit_clock_signal = clock_signal
        self.commit_clock_net = clock_net
        self.commit_ready_signal = ready_signal
        self.commit_ready_net = ready_net

    def _create_state_components(self) -> None:
        cells = {item.register_name: item for item in self.plan.state_cells}
        for register in self.module.state_registers:
            cell = cells[register.name]
            candidate = self.state_candidate_by_id[cell.candidate]
            if candidate.register_name != register.name:
                raise MappingProblemError(
                    "selected state-cell candidate belongs to another register"
                )
            if isinstance(register, FreezeRegister):
                self._lower_mapped_freeze(register, cell.base_read_phase, candidate)
            elif isinstance(register, AccumulatorRegister):
                self._lower_mapped_accumulator(register, cell.base_read_phase, candidate)
            else:  # pragma: no cover - canonical state scope
                raise MappingProblemError(
                    f"unsupported mapped register type {type(register).__name__}"
                )

    def _commit_predicate(
        self,
        *,
        raw_phase: int,
        equal: bool,
        compare_type: str = "and",
    ) -> DeciderCondition:
        if self.commit_clock_signal is None or self.commit_clock_net is None:
            raise AssertionError("periodic commit resource was not emitted")
        period = cast(int, self.problem.period)
        return DeciderCondition(
            comparator="==" if equal else "!=",
            left=Operand(signal=self.commit_clock_signal, nets=(self.commit_clock_net,)),
            right=Operand(constant=raw_phase % period + 1),
            compare_type=compare_type,
        )

    def _ready_predicate(
        self,
        *,
        ready: bool,
        compare_type: str = "and",
    ) -> DeciderCondition:
        if self.commit_ready_signal is None or self.commit_ready_net is None:
            raise AssertionError("periodic ready resource was not emitted")
        return DeciderCondition(
            comparator="!=" if ready else "==",
            left=Operand(signal=self.commit_ready_signal, nets=(self.commit_ready_net,)),
            right=Operand(constant=0),
            compare_type=compare_type,
        )

    @staticmethod
    def _value_predicate(
        value: RealizedValue,
        *,
        nonzero: bool,
        compare_type: str = "and",
    ) -> DeciderCondition:
        return DeciderCondition(
            comparator="!=" if nonzero else "==",
            left=Operand(signal=value.signal, nets=(value.net,)),
            right=Operand(constant=0),
            compare_type=compare_type,
        )

    def _emit_control_decider(
        self,
        *,
        primary: DeciderCondition,
        additional: tuple[DeciderCondition, ...],
        input_nets: tuple[int, ...],
        label: str,
    ) -> RealizedValue:
        signal = self._new_signal(label)
        entity = DeciderCombinator(
            id=self._take_entity_id(),
            comparator=primary.comparator,
            left=primary.left,
            right=primary.right,
            output_signal=signal,
            output_constant=1,
            additional_conditions=additional,
            description=label,
        )
        self.circuit.entities.append(entity)
        endpoint = Endpoint(entity.id, Connector.INPUT)
        for net in dict.fromkeys(input_nets):
            self._attach(net, endpoint)
        output_net = self._new_net(
            (signal,),
            Endpoint(entity.id, Connector.OUTPUT),
            label=label,
        )
        return RealizedValue(signal, output_net, 0)

    def _lower_mapped_freeze(
        self,
        register: FreezeRegister,
        base_phase: int,
        candidate: StateCellCandidate,
    ) -> None:
        if candidate.entity_cost != 4 or candidate.commit_phase_offset != -2:
            raise MappingProblemError(
                "mapped Freeze lowerer requires the four-entity clocked candidate"
            )
        port = candidate.transition_ports[0]
        transition = self.problem.state_transition_by_id(port.transition)
        if transition.kind != "set" or transition.value is None or transition.when is None:
            raise MappingProblemError(
                "mapped Freeze candidate does not describe one set transition"
            )
        next_read = base_phase + cast(int, self.problem.period)
        raw_phase = next_read + candidate.commit_phase_offset
        data_phase = next_read + cast(int, port.value_phase_offset)
        if port.when_phase_offset != candidate.commit_phase_offset:
            raise MappingProblemError("Freeze condition and commit predicates must share one phase")

        source_semantic = cast(VectorValue, transition.semantic.value)
        control_semantic = cast(Value, transition.semantic.when)
        source = self.delay_vector_to(self.realize_vector(source_semantic), data_phase)
        control = self.delay_to(self.realize(control_semantic), raw_phase)

        if self.commit_clock_net is None or self.commit_ready_net is None:
            raise AssertionError("periodic commit nets are missing")
        shared_nets = (control.net, self.commit_ready_net, self.commit_clock_net)
        pass_value = self._emit_control_decider(
            primary=self._value_predicate(control, nonzero=True),
            additional=(
                self._ready_predicate(ready=True),
                self._commit_predicate(raw_phase=raw_phase, equal=True),
            ),
            input_nets=shared_nets,
            label=f"Mapped FreezeReg {register.name}: pass",
        )
        hold_value = self._emit_control_decider(
            primary=self._value_predicate(control, nonzero=False),
            additional=(
                self._ready_predicate(ready=False, compare_type="or"),
                self._commit_predicate(raw_phase=raw_phase, equal=False, compare_type="or"),
            ),
            input_nets=shared_nets,
            label=f"Mapped FreezeReg {register.name}: hold",
        )
        pass_value = replace(pass_value, phase=data_phase)
        hold_value = replace(hold_value, phase=data_phase)

        self._add_net_conflict(
            source.net,
            pass_value.net,
            f"Mapped FreezeReg {register.name}: data/control isolation",
        )
        gate = ArithmeticCombinator(
            id=self._take_entity_id(),
            operation="*",
            left=Operand(each=True, nets=(source.net,)),
            right=Operand(signal=pass_value.signal, nets=(pass_value.net,)),
            output_each=True,
            description=f"Mapped FreezeReg {register.name}: input gate",
        )
        self.circuit.entities.append(gate)
        gate_input = Endpoint(gate.id, Connector.INPUT)
        self._attach(source.net, gate_input)
        self._attach(pass_value.net, gate_input)

        memory_id = self.state_memory_ids[register.name]
        memory_net = self.state_memory_nets[register.name]
        self._attach(memory_net, Endpoint(gate.id, Connector.OUTPUT))
        self._add_net_conflict(
            memory_net,
            hold_value.net,
            f"Mapped FreezeReg {register.name}: memory/hold isolation",
        )
        memory = ArithmeticCombinator(
            id=memory_id,
            operation="*",
            left=Operand(each=True, nets=(memory_net,)),
            right=Operand(signal=hold_value.signal, nets=(hold_value.net,)),
            output_each=True,
            description=f"Mapped FreezeReg {register.name}: vector memory",
        )
        self._attach(hold_value.net, Endpoint(memory_id, Connector.INPUT))
        self.circuit.entities.append(memory)

    def _lower_mapped_accumulator(
        self,
        register: AccumulatorRegister,
        base_phase: int,
        candidate: StateCellCandidate,
    ) -> None:
        if candidate.entity_cost != 4 or candidate.commit_phase_offset != -2:
            raise MappingProblemError(
                "mapped Accumulator lowerer requires the four-entity clocked candidate"
            )
        transitions = {
            self.problem.state_transition_by_id(port.transition).kind: (
                self.problem.state_transition_by_id(port.transition),
                port,
            )
            for port in candidate.transition_ports
        }
        if set(transitions) != {"add", "clear"}:
            raise MappingProblemError(
                "mapped Accumulator candidate requires add and clear transitions"
            )
        add, add_port = transitions["add"]
        clear, clear_port = transitions["clear"]
        if add.value is None or add.when is None or clear.when is None:
            raise MappingProblemError("mapped Accumulator transition ports are incomplete")
        if add_port.value_phase_offset != -1:
            raise MappingProblemError("mapped Accumulator data port must be one tick before read")
        if (
            add_port.when_phase_offset != candidate.commit_phase_offset
            or clear_port.when_phase_offset != candidate.commit_phase_offset
        ):
            raise MappingProblemError("Accumulator controls and commit must share one phase")

        next_read = base_phase + cast(int, self.problem.period)
        raw_phase = next_read + candidate.commit_phase_offset
        data_phase = next_read - 1
        source = self.delay_vector_to(
            self.realize_vector(cast(VectorValue, add.semantic.value)),
            data_phase,
        )
        add_control = self.delay_to(self.realize(cast(Value, add.semantic.when)), raw_phase)
        clear_control = self.delay_to(self.realize(cast(Value, clear.semantic.when)), raw_phase)

        if self.commit_clock_net is None or self.commit_ready_net is None:
            raise AssertionError("periodic commit nets are missing")
        all_nets = (
            add_control.net,
            clear_control.net,
            self.commit_ready_net,
            self.commit_clock_net,
        )
        active = self._emit_control_decider(
            primary=self._value_predicate(add_control, nonzero=True),
            additional=(
                self._value_predicate(clear_control, nonzero=False),
                self._ready_predicate(ready=True),
                self._commit_predicate(raw_phase=raw_phase, equal=True),
            ),
            input_nets=all_nets,
            label=f"Mapped AccumulatorReg {register.name}: add active",
        )
        retain = self._emit_control_decider(
            primary=self._value_predicate(clear_control, nonzero=False),
            additional=(
                self._ready_predicate(ready=False, compare_type="or"),
                self._commit_predicate(raw_phase=raw_phase, equal=False, compare_type="or"),
            ),
            input_nets=(clear_control.net, self.commit_ready_net, self.commit_clock_net),
            label=f"Mapped AccumulatorReg {register.name}: retain",
        )
        active = replace(active, phase=data_phase)
        retain = replace(retain, phase=data_phase)

        self._add_net_conflict(
            source.net,
            active.net,
            f"Mapped AccumulatorReg {register.name}: data/control isolation",
        )
        gate = ArithmeticCombinator(
            id=self._take_entity_id(),
            operation="*",
            left=Operand(each=True, nets=(source.net,)),
            right=Operand(signal=active.signal, nets=(active.net,)),
            output_each=True,
            description=f"Mapped AccumulatorReg {register.name}: gated add",
        )
        self.circuit.entities.append(gate)
        gate_input = Endpoint(gate.id, Connector.INPUT)
        self._attach(source.net, gate_input)
        self._attach(active.net, gate_input)

        memory_id = self.state_memory_ids[register.name]
        memory_net = self.state_memory_nets[register.name]
        self._attach(memory_net, Endpoint(gate.id, Connector.OUTPUT))
        self._add_net_conflict(
            memory_net,
            retain.net,
            f"Mapped AccumulatorReg {register.name}: memory/retain isolation",
        )
        memory = ArithmeticCombinator(
            id=memory_id,
            operation="*",
            left=Operand(each=True, nets=(memory_net,)),
            right=Operand(signal=retain.signal, nets=(retain.net,)),
            output_each=True,
            description=f"Mapped AccumulatorReg {register.name}: vector memory",
        )
        self._attach(retain.net, Endpoint(memory_id, Connector.INPUT))
        self.circuit.entities.append(memory)

    def _delay_on_bus(
        self,
        use: MappingUse,
        delivery: PlannedDelivery,
        value: RealizedValue,
    ) -> RealizedValue:
        binding = self.bus_binding_by_producer.get(delivery.producer)
        if binding is None:
            raise MappingProblemError("BUS_TRANSPORT delivery has no mapped bus lane")
        bus, lane = binding
        if delivery.transport_start_phase != lane.start_phase:
            raise MappingProblemError("mapped bus delivery starts at the wrong phase")
        if value.phase > lane.start_phase:
            raise MappingProblemError("mapped bus producer appears after its lane start")
        start = RealizedValue(
            value.signal,
            value.net,
            lane.start_phase,
            value.clean_single_lane,
        )

        if delivery.phase == lane.start_phase + 1:
            cached = self.bus_short_branch_by_use.get(use)
            if cached is not None:
                return cached
            result = self._copy_scalar(
                start,
                delivery.phase,
                f"mapped state bus {bus.index} short branch {lane.producer}",
            )
            self.bus_short_branch_by_use[use] = result
            return result
        if delivery.phase < lane.start_phase + 2:
            raise MappingProblemError("mapped bus long egress precedes isolated ingress")

        ingress = self._bus_ingress(bus, lane, start)
        self._register_bus_join(bus, lane, ingress)
        trunk_phase = delivery.phase - 1
        if trunk_phase == ingress.phase:
            trunk = ingress
        else:
            for phase in range(ingress.phase, trunk_phase):
                self._ensure_bus_stage(bus, phase)
            output_net = self.bus_stage_output_net[(bus.index, trunk_phase - 1)]
            trunk = RealizedValue(ingress.signal, output_net, trunk_phase, False)
        return self._bus_egress(use, bus, lane, trunk, delivery.phase)

    def _copy_scalar(
        self,
        value: RealizedValue,
        output_phase: int,
        description: str,
    ) -> RealizedValue:
        if value.phase + 1 != output_phase:
            raise MappingProblemError("mapped scalar copy must advance one tick")
        signal = self._new_signal(description)
        entity = ArithmeticCombinator(
            id=self._take_entity_id(),
            operation="+",
            left=Operand(signal=value.signal, nets=(value.net,)),
            right=Operand(constant=0),
            output_each=False,
            output_signal=signal,
            description=description,
        )
        self.circuit.entities.append(entity)
        self._attach(value.net, Endpoint(entity.id, Connector.INPUT))
        net = self._new_net(
            (signal,),
            Endpoint(entity.id, Connector.OUTPUT),
            label=description,
        )
        return RealizedValue(signal, net, output_phase)

    def _bus_ingress(
        self,
        bus: DelayBusResource,
        lane: DelayBusLane,
        value: RealizedValue,
    ) -> RealizedValue:
        key = (bus.index, lane.producer)
        cached = self.bus_ingress_by_lane.get(key)
        if cached is not None:
            return cached
        signal = self._new_signal(f"mapped state bus {bus.index} lane {lane.producer}")
        entity = ArithmeticCombinator(
            id=self._take_entity_id(),
            operation="+",
            left=Operand(signal=value.signal, nets=(value.net,)),
            right=Operand(constant=0),
            output_each=False,
            output_signal=signal,
            description="mapped state delay bus ingress",
        )
        self.circuit.entities.append(entity)
        self._attach(value.net, Endpoint(entity.id, Connector.INPUT))
        output_phase = value.phase + 1
        if output_phase != lane.ingress_phase:
            raise MappingProblemError("mapped bus ingress latency disagrees with plan")
        endpoint = Endpoint(entity.id, Connector.OUTPUT)
        phase_key = (bus.index, output_phase)
        net = self.bus_ingress_net_by_phase.get(phase_key)
        if net is None:
            net = self._new_net(
                (signal,),
                endpoint,
                label=f"mapped state bus {bus.index} ingress @ {output_phase}",
            )
            self.bus_ingress_net_by_phase[phase_key] = net
        else:
            self._attach(net, endpoint)
            self._append_bus_signal(net, signal)
        result = RealizedValue(signal, net, output_phase, False)
        self.bus_ingress_by_lane[key] = result
        return result

    def _bus_egress(
        self,
        use: MappingUse,
        bus: DelayBusResource,
        lane: DelayBusLane,
        trunk: RealizedValue,
        target_phase: int,
    ) -> RealizedValue:
        cached = self.bus_egress_by_use.get(use)
        if cached is not None:
            return cached
        result = self._copy_scalar(
            trunk,
            target_phase,
            f"mapped state bus {bus.index} egress {lane.producer}",
        )
        self.bus_egress_by_use[use] = result
        return result

    def _register_bus_join(
        self,
        bus: DelayBusResource,
        lane: DelayBusLane,
        value: RealizedValue,
    ) -> None:
        key = (bus.index, lane.producer)
        if key in self.joined_bus_lanes:
            return
        if value.phase != lane.ingress_phase:
            raise MappingProblemError("mapped bus join must use isolated ingress")
        self.joined_bus_lanes.add(key)
        self.bus_joins[(bus.index, value.phase)].append((value.net, cast(int, value.signal)))
        if (bus.index, value.phase) in self.bus_stage_entity_index:
            self._add_bus_stage_input(bus.index, value.phase, value.net)
            self._propagate_bus_signal(bus.index, value.phase, cast(int, value.signal))

    def _ensure_bus_stage(self, bus: DelayBusResource, phase: int) -> None:
        key = (bus.index, phase)
        if key in self.bus_stage_entity_index:
            return
        if not bus.middle_start_phase <= phase < bus.middle_end_phase:
            raise MappingProblemError("mapped bus stage lies outside selected middle span")
        inputs: list[int] = []
        previous_net = self.bus_stage_output_net.get((bus.index, phase - 1))
        if previous_net is not None:
            inputs.append(previous_net)
        joins = self.bus_joins.get(key, ())
        inputs.extend(net for net, _signal in joins)
        inputs = list(dict.fromkeys(inputs))
        if not inputs:
            raise MappingProblemError(
                f"mapped state bus {bus.index} has no lane feeding stage {phase}->{phase + 1}"
            )
        signals: set[int] = set()
        if previous_net is not None:
            signals.update(self.net_builders[previous_net].signals)
        signals.update(signal for _net, signal in joins)

        entity = ArithmeticCombinator(
            id=self._take_entity_id(),
            operation="+",
            left=Operand(each=True, nets=tuple(inputs)),
            right=Operand(constant=0),
            output_each=True,
            description=f"mapped state shared delay bus {bus.index}",
        )
        self.circuit.entities.append(entity)
        self.bus_stage_entity_index[key] = len(self.circuit.entities) - 1
        endpoint = Endpoint(entity.id, Connector.INPUT)
        for net in inputs:
            self._attach(net, endpoint)
        output_net = self._new_net(
            tuple(sorted(signals)),
            Endpoint(entity.id, Connector.OUTPUT),
            label=f"mapped state bus {bus.index} @ {phase + 1}",
        )
        self.bus_stage_output_net[key] = output_net
        self._add_all_signal_conflicts(output_net)
        next_key = (bus.index, phase + 1)
        if next_key in self.bus_stage_entity_index:
            self._add_bus_stage_input(bus.index, phase + 1, output_net)
            for signal in signals:
                self._propagate_bus_signal(bus.index, phase + 1, signal)

    def _add_bus_stage_input(self, bus: int, phase: int, net: int) -> None:
        index = self.bus_stage_entity_index[(bus, phase)]
        entity = self.circuit.entities[index]
        if not isinstance(entity, ArithmeticCombinator) or not entity.left.each:
            raise AssertionError("mapped bus stage is not an Each combinator")
        if net in entity.left.nets:
            return
        self.circuit.entities[index] = replace(
            entity,
            left=Operand(each=True, nets=(*entity.left.nets, net)),
        )
        self._attach(net, Endpoint(entity.id, Connector.INPUT))

    def _propagate_bus_signal(self, bus: int, start_phase: int, signal: int) -> None:
        phase = start_phase
        while (bus, phase) in self.bus_stage_output_net:
            self._append_bus_signal(self.bus_stage_output_net[(bus, phase)], signal)
            phase += 1

    def _append_bus_signal(self, net: int, signal: int) -> None:
        builder = self.net_builders[net]
        if signal in builder.signals:
            return
        for existing in builder.signals:
            self._add_signal_conflict(
                existing,
                signal,
                "mapped state delay-bus lanes coexist on one carrier",
            )
        builder.signals = tuple(sorted((*builder.signals, signal)))

    def _add_all_signal_conflicts(self, net: int) -> None:
        signals = self.net_builders[net].signals
        for index, left in enumerate(signals):
            for right in signals[index + 1 :]:
                self._add_signal_conflict(
                    left,
                    right,
                    "mapped state delay-bus lanes coexist on one carrier",
                )


def lower_periodic_state_mapping_plan(
    module: CircuitModule,
    problem: MappingProblem,
    candidates: tuple[ImplementationCandidate, ...],
    state_candidates: tuple[StateCellCandidate, ...],
    plan: RealizationPlan,
) -> PeriodicStatePhysicalLoweringResult:
    """Lower one clocked periodic mapping plan without invoking state-timing analysis."""

    return _MappedPeriodicStateLowerer(
        module,
        problem,
        candidates,
        state_candidates,
        plan,
    ).lower_mapped()


__all__ = ["PeriodicStatePhysicalLoweringResult", "lower_periodic_state_mapping_plan"]
