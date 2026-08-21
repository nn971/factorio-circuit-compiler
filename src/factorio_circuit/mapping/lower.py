"""Deterministic mapping-plan lowering to Abstract Physical IR.

This lowerer intentionally supports a narrow stateless scalar subset: external scalar Level inputs,
scalar constants, ordinary ``BinaryOp``/``Compare`` implementations, private exact transport, the
conservative zero-delay ``WIRE_SUM`` candidate, and isolated shared scalar delay buses selected by
the joint mapper. It consumes an already validated :class:`RealizationPlan`; candidate selection and
physical phases are never recomputed here.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from factorio_circuit.ir.abstract_physical import (
    AbstractNet,
    AbstractPhysicalCircuit,
    AbstractSignal,
    ArithmeticCombinator,
    Connector,
    ConstantCombinator,
    DeciderCombinator,
    Endpoint,
    InputPort,
    Operand,
    OutputPort,
    SignalConflict,
    SignalDomain,
)
from factorio_circuit.ir.semantic import (
    BinaryOp,
    CircuitModule,
    Compare,
    Constant,
    FlowInput,
    FlowInputSample,
    Input,
    InputSample,
    PayloadShape,
    reject_event_module,
    validate_canonical_module,
)

from .plan import (
    DelayBusLane,
    DelayBusResource,
    DeliveryKind,
    PlannedDelivery,
    RealizationPlan,
    WireSumResource,
)
from .problem import MappingProblem, MappingProblemError, MappingSource, MappingUse
from .templates import ImplementationCandidate, ImplementationKind
from .validate import validate_realization_plan


@dataclass(frozen=True, slots=True)
class _Value:
    signal: int
    net: int
    phase: int


@dataclass(slots=True)
class _NetBuilder:
    signals: tuple[int, ...]
    endpoints: list[Endpoint] = field(default_factory=list)
    label: str | None = None


type _BusBinding = tuple[DelayBusResource, DelayBusLane]


class _MappedScalarLowerer:
    def __init__(
        self,
        module: CircuitModule,
        problem: MappingProblem,
        candidates: tuple[ImplementationCandidate, ...],
        plan: RealizationPlan,
    ) -> None:
        reject_event_module(module)
        validate_canonical_module(module)
        validate_realization_plan(problem, candidates, plan)
        if module.state_registers:
            raise MappingProblemError("mapped scalar lowering is stateless in the first milestone")
        if any(source.shape is not PayloadShape.SCALAR for source in problem.sources):
            raise MappingProblemError("mapped scalar lowering does not yet support vector sources")
        if any(operation.shape is not PayloadShape.SCALAR for operation in problem.operations):
            raise MappingProblemError(
                "mapped scalar lowering does not yet support vector operations"
            )
        if len(problem.sinks) != len(module.output.values):
            raise MappingProblemError(
                "mapping sinks must correspond one-to-one with module outputs"
            )

        self.module = module
        self.problem = problem
        self.plan = plan
        self.circuit = AbstractPhysicalCircuit(name=module.name)
        self.candidate_by_id = {item.id: item for item in candidates}
        self.operation_by_id = {item.id: item for item in problem.operations}
        self.source_by_id = {item.id: item for item in problem.sources}
        self.realization_by_operation = {item.operation: item for item in plan.realizations}
        self.delivery_by_use = {
            MappingUse(item.producer, item.consumer, item.operand_index): item
            for item in plan.deliveries
        }
        self.wire_sum_by_operation = {item.operation: item for item in plan.wire_sums}
        self.contribution_target: dict[int, WireSumResource] = {}
        for resource in plan.wire_sums:
            for producer in (resource.left_producer, resource.right_producer):
                previous = self.contribution_target.setdefault(producer, resource)
                if previous != resource:
                    raise MappingProblemError(
                        "one physical realization cannot contribute to two first-milestone "
                        "wire sums"
                    )

        self.bus_binding_by_producer: dict[int, _BusBinding] = {}
        for bus in plan.delay_buses:
            for lane in bus.lanes:
                previous = self.bus_binding_by_producer.setdefault(lane.producer, (bus, lane))
                if previous != (bus, lane):
                    raise MappingProblemError("one producer cannot use two mapped delay buses")

        self.next_entity_id = 1
        self.next_signal_id = 1
        self.next_net_id = 1
        self.net_builders: dict[int, _NetBuilder] = {}
        self.input_by_semantic: dict[int, _Value] = {}
        self.source_values: dict[int, _Value] = {}
        self.operation_values: dict[int, _Value] = {}
        self.delay_values: dict[tuple[int, int], _Value] = {}
        self.wire_sum_networks: dict[int, _Value] = {}

        self.bus_ingress_by_lane: dict[tuple[int, int], _Value] = {}
        self.bus_ingress_net_by_phase: dict[tuple[int, int], int] = {}
        self.bus_short_branch_by_use: dict[MappingUse, _Value] = {}
        self.bus_egress_by_use: dict[MappingUse, _Value] = {}
        self.bus_joins: dict[tuple[int, int], list[tuple[int, int]]] = {}
        self.joined_bus_lanes: set[tuple[int, int]] = set()
        self.bus_stage_entity_index: dict[tuple[int, int], int] = {}
        self.bus_stage_output_net: dict[tuple[int, int], int] = {}

    def lower(self) -> AbstractPhysicalCircuit:
        self._create_input_markers()
        for sink in self.problem.sinks:
            use = MappingUse(sink.value, sink.id, None)
            delivery = self.delivery_by_use[use]
            value = self._value_for_delivery(use, delivery)
            marker = ConstantCombinator(
                id=self._take_entity_id(),
                description=f"OUTPUT {sink.label} — phase +{sink.phase} tick(s)",
                annotation_only=True,
            )
            self.circuit.entities.append(marker)
            endpoint = Endpoint(marker.id, Connector.SINGLE)
            self._attach(value.net, endpoint)
            self.circuit.outputs.append(OutputPort(sink.label, endpoint, value.signal, sink.phase))

        # The selected resource pays for one continuous Each trunk between its earliest ingress and
        # latest trunk tap. Lazy per-use realization above may have built only fragments, especially
        # for temporally disjoint lanes. Complete every charged middle stage now, after all semantic
        # uses have registered their ingress joins, so emitted hardware exactly matches plan cost.
        for bus in self.plan.delay_buses:
            for phase in range(bus.middle_start_phase, bus.middle_end_phase):
                self._ensure_bus_stage(bus, phase)

        self.circuit.nets = [
            AbstractNet(
                id=net_id,
                signals=builder.signals,
                endpoints=tuple(builder.endpoints),
                label=builder.label,
            )
            for net_id, builder in sorted(self.net_builders.items())
        ]
        self.circuit.validate()
        return self.circuit

    def _create_input_markers(self) -> None:
        for item in self.module.inputs:
            signal = self._new_signal(item.name)
            marker = ConstantCombinator(
                id=self._take_entity_id(),
                description=f"INPUT {item.name} — signal allocated during physical synthesis",
                annotation_only=True,
            )
            self.circuit.entities.append(marker)
            endpoint = Endpoint(marker.id, Connector.SINGLE)
            net = self._new_net((signal,), label=f"input {item.name}")
            self._attach(net, endpoint)
            value = _Value(signal, net, 0)
            self.input_by_semantic[id(item)] = value
            self.circuit.inputs.append(InputPort(item.name, endpoint, signal))

    def _source_value(self, source: MappingSource) -> _Value:
        cached = self.source_values.get(source.id)
        if cached is not None:
            return cached

        semantic = source.semantic
        if isinstance(semantic, FlowInput):
            base = self.input_by_semantic.get(id(semantic.source))
        elif isinstance(semantic, Input):
            base = self.input_by_semantic.get(id(semantic))
        elif isinstance(semantic, (FlowInputSample, InputSample)):
            if semantic.offset != 0:
                raise MappingProblemError("mapped scalar lowering requires offset-zero samples")
            base = self.input_by_semantic.get(id(semantic.source))
        elif isinstance(semantic, Constant):
            signal = self._new_signal(source.label)
            entity = ConstantCombinator(
                id=self._take_entity_id(),
                signals=((signal, semantic.value),),
                description=f"constant {semantic.value}",
            )
            self.circuit.entities.append(entity)
            net = self._new_net((signal,), label=source.label)
            self._attach(net, Endpoint(entity.id, Connector.SINGLE))
            base = _Value(signal, net, source.start_phase)
        else:
            raise MappingProblemError(
                f"mapped scalar lowering does not support source {type(semantic).__name__}"
            )

        if base is None:
            raise MappingProblemError(
                f"mapping source {source.label!r} has no physical input marker"
            )
        if source.start_phase != 0:
            raise MappingProblemError(
                "first-milestone mapped scalar lowering requires phase-zero physical sources"
            )
        result = _Value(base.signal, base.net, source.start_phase)
        self.source_values[source.id] = result
        return result

    def _operation_value(self, operation_id: int) -> _Value:
        cached = self.operation_values.get(operation_id)
        if cached is not None:
            return cached

        operation = self.operation_by_id[operation_id]
        realization = self.realization_by_operation[operation_id]
        candidate = self.candidate_by_id[realization.candidate]
        semantic = operation.semantic

        if candidate.kind is ImplementationKind.WIRE_SUM:
            resource = self.wire_sum_by_operation.get(operation_id)
            if resource is None:
                raise MappingProblemError("selected wire-sum candidate has no plan resource")
            result = self._wire_sum_value(resource)
        elif candidate.kind is ImplementationKind.ORDINARY:
            if isinstance(semantic, BinaryOp):
                left = self._operand_delivery(operation_id, 0)
                right = self._operand_delivery(operation_id, 1)
                result = self._emit_binary(
                    semantic.op,
                    left,
                    right,
                    realization.output_phase,
                    operation_id,
                    operation.label,
                )
            elif isinstance(semantic, Compare):
                left = self._operand_delivery(operation_id, 0)
                right = self._operand_delivery(operation_id, 1)
                result = self._emit_compare(
                    semantic.op,
                    left,
                    right,
                    realization.output_phase,
                    operation_id,
                    operation.label,
                )
            else:
                raise MappingProblemError(
                    "first mapped physical lowerer supports ordinary BinaryOp/Compare only"
                )
        else:
            raise MappingProblemError(
                f"mapped scalar lowering does not support candidate kind {candidate.kind.value!r}"
            )

        if result.phase != realization.output_phase:
            raise MappingProblemError(
                f"operation {operation.label!r} lowered at phase {result.phase}, "
                f"but the realization plan requires {realization.output_phase}"
            )
        self.operation_values[operation_id] = result
        return result

    def _operand_delivery(self, consumer: int, operand_index: int) -> _Value:
        operation = self.operation_by_id[consumer]
        producer = operation.operands[operand_index]
        use = MappingUse(producer, consumer, operand_index)
        delivery = self.delivery_by_use[use]
        return self._value_for_delivery(use, delivery)

    def _value_for_delivery(self, use: MappingUse, delivery: PlannedDelivery) -> _Value:
        if delivery.producer in self.source_by_id:
            base = self._source_value(self.source_by_id[delivery.producer])
        else:
            base = self._operation_value(delivery.producer)

        if delivery.kind in {DeliveryKind.REUSE, DeliveryKind.OBSERVE_AT}:
            return _Value(base.signal, base.net, delivery.phase)
        if delivery.transport_start_phase is None:
            raise MappingProblemError("transport delivery has no exact start phase")
        if delivery.kind is DeliveryKind.PRIVATE_TRANSPORT:
            return self._exact_transport(
                delivery.producer,
                base,
                delivery.transport_start_phase,
                delivery.phase,
            )
        if delivery.kind is DeliveryKind.BUS_TRANSPORT:
            return self._delay_on_bus(use, delivery, base)
        raise MappingProblemError(f"unsupported delivery kind {delivery.kind.value!r}")

    def _exact_transport(
        self,
        producer: int,
        base: _Value,
        start_phase: int,
        target_phase: int,
    ) -> _Value:
        if not start_phase <= target_phase:
            raise MappingProblemError("exact transport cannot move backwards")
        key = (producer, start_phase)
        current = self.delay_values.get(key)
        if current is None:
            current = _Value(base.signal, base.net, start_phase)
            self.delay_values[key] = current
        while current.phase < target_phase:
            next_phase = current.phase + 1
            cached = self.delay_values.get((producer, next_phase))
            if cached is not None:
                current = cached
                continue
            current = self._copy_scalar(
                current,
                next_phase,
                f"mapped exact transport {producer} -> phase {next_phase}",
                f"mapped exact {producer} @ {next_phase}",
            )
            self.delay_values[(producer, next_phase)] = current
        return current

    def _copy_scalar(
        self,
        value: _Value,
        output_phase: int,
        description: str,
        net_label: str,
    ) -> _Value:
        if value.phase + 1 != output_phase:
            raise MappingProblemError("scalar copy must advance exactly one physical tick")
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
        net = self._new_net((signal,), label=net_label)
        self._attach(net, Endpoint(entity.id, Connector.OUTPUT))
        return _Value(signal, net, output_phase)

    def _delay_on_bus(
        self,
        use: MappingUse,
        delivery: PlannedDelivery,
        base: _Value,
    ) -> _Value:
        binding = self.bus_binding_by_producer.get(delivery.producer)
        if binding is None:
            raise MappingProblemError("BUS_TRANSPORT delivery has no mapped delay-bus lane")
        bus, lane = binding
        if delivery.transport_start_phase != lane.start_phase:
            raise MappingProblemError("delay-bus delivery starts at the wrong exact phase")
        start = _Value(base.signal, base.net, lane.start_phase)

        if delivery.phase == lane.start_phase + 1:
            cached = self.bus_short_branch_by_use.get(use)
            if cached is not None:
                return cached
            result = self._copy_scalar(
                start,
                delivery.phase,
                f"mapped delay bus {bus.index} short branch {lane.producer}",
                f"mapped delay bus {bus.index} short egress {lane.producer}",
            )
            self.bus_short_branch_by_use[use] = result
            return result
        if delivery.phase < lane.start_phase + 2:
            raise MappingProblemError("delay-bus long egress is earlier than its isolated ingress")

        ingress = self._bus_ingress(bus, lane, start)
        self._register_bus_join(bus, lane, ingress)
        trunk_phase = delivery.phase - 1
        if trunk_phase == ingress.phase:
            trunk = ingress
        else:
            for phase in range(ingress.phase, trunk_phase):
                self._ensure_bus_stage(bus, phase)
            output_net = self.bus_stage_output_net[(bus.index, trunk_phase - 1)]
            trunk = _Value(ingress.signal, output_net, trunk_phase)
        return self._bus_egress(use, bus, lane, trunk, delivery.phase)

    def _bus_ingress(
        self,
        bus: DelayBusResource,
        lane: DelayBusLane,
        value: _Value,
    ) -> _Value:
        key = (bus.index, lane.producer)
        cached = self.bus_ingress_by_lane.get(key)
        if cached is not None:
            return cached
        if value.phase != lane.start_phase:
            raise MappingProblemError("mapped delay-bus ingress starts at the wrong phase")

        signal = self._new_signal(f"mapped delay bus {bus.index} lane {lane.producer}")
        entity = ArithmeticCombinator(
            id=self._take_entity_id(),
            operation="+",
            left=Operand(signal=value.signal, nets=(value.net,)),
            right=Operand(constant=0),
            output_each=False,
            output_signal=signal,
            description="mapped delay bus ingress",
        )
        self.circuit.entities.append(entity)
        self._attach(value.net, Endpoint(entity.id, Connector.INPUT))

        output_phase = value.phase + 1
        if output_phase != lane.ingress_phase:
            raise MappingProblemError("mapped delay-bus ingress latency disagrees with plan")
        endpoint = Endpoint(entity.id, Connector.OUTPUT)
        ingress_key = (bus.index, output_phase)
        net = self.bus_ingress_net_by_phase.get(ingress_key)
        if net is None:
            net = self._new_net(
                (signal,),
                label=f"mapped delay bus {bus.index} ingress @ {output_phase}",
            )
            self._attach(net, endpoint)
            self.bus_ingress_net_by_phase[ingress_key] = net
        else:
            self._attach(net, endpoint)
            self._append_bus_signal(net, signal)

        result = _Value(signal, net, output_phase)
        self.bus_ingress_by_lane[key] = result
        return result

    def _bus_egress(
        self,
        use: MappingUse,
        bus: DelayBusResource,
        lane: DelayBusLane,
        trunk: _Value,
        target_phase: int,
    ) -> _Value:
        cached = self.bus_egress_by_use.get(use)
        if cached is not None:
            return cached
        if trunk.phase + 1 != target_phase:
            raise MappingProblemError("mapped delay-bus egress must consume the prior tick")

        result = self._copy_scalar(
            trunk,
            target_phase,
            f"mapped delay bus {bus.index} egress {lane.producer}",
            f"mapped delay bus {bus.index} isolated egress {lane.producer} @ {target_phase}",
        )
        self.bus_egress_by_use[use] = result
        return result

    def _register_bus_join(
        self,
        bus: DelayBusResource,
        lane: DelayBusLane,
        value: _Value,
    ) -> None:
        key = (bus.index, lane.producer)
        if key in self.joined_bus_lanes:
            return
        if value.phase != lane.ingress_phase:
            raise MappingProblemError("mapped delay-bus join must use its isolated ingress")
        self.joined_bus_lanes.add(key)
        self.bus_joins.setdefault((bus.index, value.phase), []).append((value.net, value.signal))

        stage_key = (bus.index, value.phase)
        if stage_key in self.bus_stage_entity_index:
            self._add_bus_stage_input(bus.index, value.phase, value.net)
            self._propagate_bus_signal(bus.index, value.phase, value.signal)

    def _ensure_bus_stage(self, bus: DelayBusResource, phase: int) -> None:
        key = (bus.index, phase)
        if key in self.bus_stage_entity_index:
            return
        if not bus.middle_start_phase <= phase < bus.middle_end_phase:
            raise MappingProblemError("mapped delay-bus stage lies outside selected middle span")

        inputs: list[int] = []
        previous_net = self.bus_stage_output_net.get((bus.index, phase - 1))
        if previous_net is not None:
            inputs.append(previous_net)
        joins = self.bus_joins.get(key, ())
        inputs.extend(net for net, _signal in joins)
        inputs = list(dict.fromkeys(inputs))
        if not inputs:
            raise MappingProblemError(
                f"mapped delay bus {bus.index} has no lane feeding stage {phase}->{phase + 1}"
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
            description=f"mapped shared delay bus {bus.index}",
        )
        self.circuit.entities.append(entity)
        self.bus_stage_entity_index[key] = len(self.circuit.entities) - 1
        endpoint = Endpoint(entity.id, Connector.INPUT)
        for net in inputs:
            self._attach(net, endpoint)

        output_net = self._new_net(
            tuple(sorted(signals)),
            label=f"mapped shared delay bus {bus.index} @ {phase + 1}",
        )
        self._attach(output_net, Endpoint(entity.id, Connector.OUTPUT))
        self.bus_stage_output_net[key] = output_net
        self._add_all_signal_conflicts(output_net)

        next_key = (bus.index, phase + 1)
        if next_key in self.bus_stage_entity_index:
            self._add_bus_stage_input(bus.index, phase + 1, output_net)
            for signal in signals:
                self._propagate_bus_signal(bus.index, phase + 1, signal)

    def _add_bus_stage_input(self, bus: int, phase: int, net: int) -> None:
        key = (bus, phase)
        index = self.bus_stage_entity_index[key]
        entity = self.circuit.entities[index]
        if not isinstance(entity, ArithmeticCombinator) or not entity.left.each:
            raise AssertionError("mapped delay-bus stage is not an Each combinator")
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
                "mapped shared delay-bus lanes coexist on one carrier",
            )
        builder.signals = tuple(sorted((*builder.signals, signal)))

    def _add_all_signal_conflicts(self, net: int) -> None:
        signals = self.net_builders[net].signals
        for index, left in enumerate(signals):
            for right in signals[index + 1 :]:
                self._add_signal_conflict(
                    left,
                    right,
                    "mapped shared delay-bus lanes coexist on one carrier",
                )

    def _add_signal_conflict(self, left: int, right: int, reason: str) -> None:
        if left == right:
            raise MappingProblemError("one mapped delay-bus net reused an abstract lane identity")
        ordered = (min(left, right), max(left, right))
        for conflict in self.circuit.signal_conflicts:
            if (min(conflict.left, conflict.right), max(conflict.left, conflict.right)) == ordered:
                return
        self.circuit.signal_conflicts.append(SignalConflict(ordered[0], ordered[1], reason))

    def _emit_binary(
        self,
        operation: str,
        left: _Value,
        right: _Value,
        output_phase: int,
        operation_id: int,
        label: str,
    ) -> _Value:
        if left.phase + 1 != output_phase or right.phase + 1 != output_phase:
            raise MappingProblemError("ordinary scalar binary timing disagrees with physical stage")
        signal, net = self._output_resource(operation_id, label, output_phase)
        entity = ArithmeticCombinator(
            id=self._take_entity_id(),
            operation=operation,
            left=Operand(signal=left.signal, nets=(left.net,)),
            right=Operand(signal=right.signal, nets=(right.net,)),
            output_each=False,
            output_signal=signal,
            description=label,
        )
        self.circuit.entities.append(entity)
        endpoint = Endpoint(entity.id, Connector.INPUT)
        self._attach(left.net, endpoint)
        self._attach(right.net, endpoint)
        self._attach(net, Endpoint(entity.id, Connector.OUTPUT))
        return _Value(signal, net, output_phase)

    def _emit_compare(
        self,
        comparator: str,
        left: _Value,
        right: _Value,
        output_phase: int,
        operation_id: int,
        label: str,
    ) -> _Value:
        if left.phase + 1 != output_phase or right.phase + 1 != output_phase:
            raise MappingProblemError("ordinary comparison timing disagrees with physical stage")
        signal, net = self._output_resource(operation_id, label, output_phase)
        entity = DeciderCombinator(
            id=self._take_entity_id(),
            comparator=comparator,
            left=Operand(signal=left.signal, nets=(left.net,)),
            right=Operand(signal=right.signal, nets=(right.net,)),
            output_signal=signal,
            output_constant=1,
            description=label,
        )
        self.circuit.entities.append(entity)
        endpoint = Endpoint(entity.id, Connector.INPUT)
        self._attach(left.net, endpoint)
        self._attach(right.net, endpoint)
        self._attach(net, Endpoint(entity.id, Connector.OUTPUT))
        return _Value(signal, net, output_phase)

    def _output_resource(self, operation_id: int, label: str, phase: int) -> tuple[int, int]:
        resource = self.contribution_target.get(operation_id)
        if resource is not None:
            if resource.phase != phase:
                raise MappingProblemError(
                    "wire-sum contribution is not produced at the shared phase"
                )
            shared = self._ensure_wire_sum_network(resource)
            return shared.signal, shared.net
        signal = self._new_signal(label)
        net = self._new_net((signal,), label=label)
        return signal, net

    def _wire_sum_value(self, resource: WireSumResource) -> _Value:
        shared = self._ensure_wire_sum_network(resource)
        left = self._operation_value(resource.left_producer)
        right = self._operation_value(resource.right_producer)
        if (
            left.signal != shared.signal
            or right.signal != shared.signal
            or left.net != shared.net
            or right.net != shared.net
            or left.phase != resource.phase
            or right.phase != resource.phase
        ):
            raise MappingProblemError("wire-sum contributors did not lower onto the shared carrier")
        return shared

    def _ensure_wire_sum_network(self, resource: WireSumResource) -> _Value:
        cached = self.wire_sum_networks.get(resource.operation)
        if cached is not None:
            return cached
        signal = self._new_signal(f"wire sum {resource.operation}")
        net = self._new_net((signal,), label=f"wire sum {resource.operation}")
        result = _Value(signal, net, resource.phase)
        self.wire_sum_networks[resource.operation] = result
        return result

    def _take_entity_id(self) -> int:
        result = self.next_entity_id
        self.next_entity_id += 1
        return result

    def _new_signal(self, label: str | None = None) -> int:
        result = self.next_signal_id
        self.next_signal_id += 1
        self.circuit.signals.append(
            AbstractSignal(result, label=label, domain=SignalDomain.VIRTUAL)
        )
        return result

    def _new_net(self, signals: tuple[int, ...], *, label: str | None = None) -> int:
        result = self.next_net_id
        self.next_net_id += 1
        self.net_builders[result] = _NetBuilder(signals=signals, label=label)
        return result

    def _attach(self, net: int, endpoint: Endpoint) -> None:
        builder = self.net_builders[net]
        if endpoint not in builder.endpoints:
            builder.endpoints.append(endpoint)


def lower_stateless_mapping_plan(
    module: CircuitModule,
    problem: MappingProblem,
    candidates: tuple[ImplementationCandidate, ...],
    plan: RealizationPlan,
) -> AbstractPhysicalCircuit:
    """Lower one validated stateless scalar mapping plan to Abstract Physical IR."""

    return _MappedScalarLowerer(module, problem, candidates, plan).lower()
