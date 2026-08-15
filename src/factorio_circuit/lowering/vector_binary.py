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


def realize_vector_binary(lowerer: Any, value: VectorBinaryOp) -> RealizedVector:
    left = lowerer.realize_vector(value.left)
    right = lowerer.realize_vector(value.right)
    phase = max(left.phase, right.phase)
    left = lowerer.delay_vector_to(left, phase)
    right = lowerer.delay_vector_to(right, phase)
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
