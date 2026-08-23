"""Lower lane-wise binary operations on runtime-open vectors."""

from __future__ import annotations

from typing import Any

from factorio_circuit.analysis.latency import FACTORIO_LATENCY
from factorio_circuit.ir.abstract_physical import ArithmeticCombinator, Connector, Endpoint, Operand
from factorio_circuit.ir.physical import SignalId
from factorio_circuit.ir.semantic import VectorBinaryOp
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedVector


def vector_metadata(lowerer: Any, *net_ids: int) -> tuple[tuple[SignalId, ...], bool]:
    builders = [lowerer.net_builders[net_id] for net_id in net_ids]
    dynamic = any(builder.carries_dynamic_vector for builder in builders)
    if dynamic:
        return (), True
    fixed = {signal for builder in builders for signal in builder.fixed_signals}
    return tuple(sorted(fixed, key=lambda signal: (signal.kind, signal.name))), False


def _mapped_wire_sum_resource(lowerer: Any, value: VectorBinaryOp) -> tuple[int, Any] | None:
    """Return mapped wire-sum metadata when this vector addition selected that technology."""

    operation_ids = getattr(lowerer, "operation_id_by_semantic", None)
    realizations = getattr(lowerer, "realization_by_operation", None)
    candidates = getattr(lowerer, "candidate_by_id", None)
    plan = getattr(lowerer, "plan", None)
    problem = getattr(lowerer, "problem", None)
    if not all(item is not None for item in (operation_ids, realizations, candidates, plan, problem)):
        return None

    operation_id = operation_ids.get(id(value))
    if operation_id is None:
        return None
    realization = realizations[operation_id]
    candidate = candidates[realization.candidate]
    if candidate.kind != "wire-sum":
        return None
    if value.op != "+" or candidate.entity_cost != 0:
        raise ValueError("mapped vector wire-sum candidate metadata is invalid")

    resources = [item for item in plan.wire_sums if item.operation == operation_id]
    if len(resources) != 1:
        raise ValueError("mapped vector wire sum requires exactly one plan resource")
    resource = resources[0]
    mapping_operation = problem.operation_by_id(operation_id)
    if tuple(mapping_operation.operands) != (
        resource.left_producer,
        resource.right_producer,
    ):
        raise ValueError("mapped vector wire-sum resource has the wrong contributors")
    if resource.phase != realization.output_phase:
        raise ValueError("mapped vector wire-sum resource has the wrong output phase")
    return operation_id, resource


def _realize_mapped_vector_wire_sum(
    lowerer: Any,
    value: VectorBinaryOp,
    left: RealizedVector,
    right: RealizedVector,
    phase: int,
) -> RealizedVector | None:
    selected = _mapped_wire_sum_resource(lowerer, value)
    if selected is None:
        return None
    operation_id, resource = selected
    if phase != resource.phase or left.phase != phase or right.phase != phase:
        raise ValueError("mapped vector wire-sum contributors are not on the shared phase")
    if left.net == right.net:
        raise ValueError("mapped vector wire sum requires two distinct producer output nets")

    source_nets = (left.net, right.net)
    builders = [lowerer.net_builders[net_id] for net_id in source_nets]
    for net_id, builder in zip(source_nets, builders, strict=True):
        if builder.signals:
            raise ValueError("mapped vector wire-sum contributor carries scalar abstract lanes")
        if not builder.endpoints or any(
            endpoint.connector is not Connector.OUTPUT for endpoint in builder.endpoints
        ):
            raise ValueError(
                "mapped vector wire-sum contributor was observed before aggregate formation"
            )
        if any(
            net_id in (conflict.left, conflict.right) for conflict in lowerer.circuit.net_conflicts
        ):
            raise ValueError("mapped vector wire-sum contributor already has a net-color conflict")

    fixed_signals, carries_dynamic_vector = vector_metadata(lowerer, *source_nets)
    endpoints = [endpoint for builder in builders for endpoint in builder.endpoints]
    aggregate_net = lowerer._new_net(
        (),
        endpoints[0],
        label=f"mapped vector wire sum {operation_id}",
        fixed_signals=fixed_signals,
        carries_dynamic_vector=carries_dynamic_vector,
    )
    for endpoint in endpoints[1:]:
        lowerer._attach(aggregate_net, endpoint)
    for net_id in source_nets:
        del lowerer.net_builders[net_id]

    vector_origin = getattr(lowerer, "vector_origin", None)
    if isinstance(vector_origin, dict):
        for net_id in source_nets:
            vector_origin.pop(net_id, None)
    return RealizedVector(aggregate_net, resource.phase)


def realize_vector_binary(lowerer: Any, value: VectorBinaryOp) -> RealizedVector:
    left = lowerer.realize_vector(value.left)
    right = lowerer.realize_vector(value.right)
    phase = max(left.phase, right.phase)
    schedule = getattr(lowerer, "_operation_input_phase", None)
    if schedule is not None:
        phase = schedule(value, "vector_binary", value.op, phase)
    left = lowerer.delay_vector_to(left, phase)
    right = lowerer.delay_vector_to(right, phase)

    wire_sum = _realize_mapped_vector_wire_sum(lowerer, value, left, right, phase)
    if wire_sum is not None:
        return wire_sum

    if left.net != right.net:
        lowerer._add_net_conflict(
            left.net,
            right.net,
            "runtime vector Each operands must use opposite wire colors",
        )
    entity = ArithmeticCombinator(
        id=lowerer._take_entity_id(),
        operation=value.op,
        left=Operand(each=True, nets=(left.net,)),
        right=Operand(each=True, nets=(right.net,)),
        output_each=True,
        description=f"runtime vector {value.op}",
    )
    lowerer.circuit.entities.append(entity)
    endpoint = Endpoint(entity.id, Connector.INPUT)
    lowerer._attach(left.net, endpoint)
    lowerer._attach(right.net, endpoint)
    fixed, dynamic = vector_metadata(lowerer, left.net, right.net)
    net = lowerer._new_net(
        (),
        Endpoint(entity.id, Connector.OUTPUT),
        label=f"runtime vector {value.op}",
        fixed_signals=fixed,
        carries_dynamic_vector=dynamic,
    )
    return RealizedVector(
        net, phase + FACTORIO_LATENCY.operation_latency("vector_binary", value.op)
    )
