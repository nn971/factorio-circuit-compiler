"""Deterministic first-milestone mapping plan lowering to Abstract Physical IR.

This lowerer intentionally supports a narrow stateless scalar subset: external scalar Level inputs,
scalar constants, ordinary ``BinaryOp``/``Compare`` implementations, private exact transport, and
the conservative zero-delay ``WIRE_SUM`` candidate.  It consumes an already validated
:class:`RealizationPlan`; candidate selection and physical phases are never recomputed here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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

from .plan import DeliveryKind, PlannedDelivery, RealizationPlan, WireSumResource
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
            raise MappingProblemError("mapped scalar lowering does not yet support vector operations")
        if len(problem.sinks) != len(module.output.values):
            raise MappingProblemError("mapping sinks must correspond one-to-one with module outputs")

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
                        "one physical realization cannot contribute to two first-milestone wire sums"
                    )

        self.next_entity_id = 1
        self.next_signal_id = 1
        self.next_net_id = 1
        self.net_builders: dict[int, _NetBuilder] = {}
        self.input_by_semantic: dict[int, _Value] = {}
        self.source_values: dict[int, _Value] = {}
        self.operation_values: dict[int, _Value] = {}
        self.delay_values: dict[tuple[int, int], _Value] = {}
        self.wire_sum_networks: dict[int, _Value] = {}

    def lower(self) -> AbstractPhysicalCircuit:
        self._create_input_markers()
        for sink in self.problem.sinks:
            delivery = self.delivery_by_use[MappingUse(sink.value, sink.id, None)]
            value = self._value_for_delivery(delivery)
            marker = ConstantCombinator(
                id=self._take_entity_id(),
                description=f"OUTPUT {sink.label} — phase +{sink.phase} tick(s)",
                annotation_only=True,
            )
            self.circuit.entities.append(marker)
            endpoint = Endpoint(marker.id, Connector.SINGLE)
            self._attach(value.net, endpoint)
            self.circuit.outputs.append(OutputPort(sink.label, endpoint, value.signal, sink.phase))

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
            raise MappingProblemError(f"mapping source {source.label!r} has no physical input marker")
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
        delivery = self.delivery_by_use[MappingUse(producer, consumer, operand_index)]
        return self._value_for_delivery(delivery)

    def _value_for_delivery(self, delivery: PlannedDelivery) -> _Value:
        if delivery.producer in self.source_by_id:
            base = self._source_value(self.source_by_id[delivery.producer])
        else:
            base = self._operation_value(delivery.producer)

        if delivery.kind in {DeliveryKind.REUSE, DeliveryKind.OBSERVE_AT}:
            return _Value(base.signal, base.net, delivery.phase)
        if delivery.kind is not DeliveryKind.PRIVATE_TRANSPORT:
            raise MappingProblemError(f"unsupported delivery kind {delivery.kind.value!r}")
        if delivery.transport_start_phase is None:
            raise MappingProblemError("private transport has no exact start phase")
        return self._exact_transport(
            delivery.producer,
            base,
            delivery.transport_start_phase,
            delivery.phase,
        )

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
            signal = self._new_signal(f"mapped exact transport {producer} @ {next_phase}")
            entity = ArithmeticCombinator(
                id=self._take_entity_id(),
                operation="+",
                left=Operand(signal=current.signal, nets=(current.net,)),
                right=Operand(constant=0),
                output_each=False,
                output_signal=signal,
                description=f"mapped exact transport {producer} -> phase {next_phase}",
            )
            self.circuit.entities.append(entity)
            self._attach(current.net, Endpoint(entity.id, Connector.INPUT))
            net = self._new_net((signal,), label=f"mapped exact {producer} @ {next_phase}")
            self._attach(net, Endpoint(entity.id, Connector.OUTPUT))
            current = _Value(signal, net, next_phase)
            self.delay_values[(producer, next_phase)] = current
        return current

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
                raise MappingProblemError("wire-sum contribution is not produced at the shared phase")
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
