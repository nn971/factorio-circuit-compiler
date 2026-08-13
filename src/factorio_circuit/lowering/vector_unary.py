"""Lower scalar transforms and filters on runtime-open vectors."""

from __future__ import annotations

from typing import Any

from factorio_circuit.frontend import _VectorFilter, _VectorScalarOp
from factorio_circuit.ir.abstract_physical import (
    ArithmeticCombinator,
    Connector,
    DeciderCombinator,
    Endpoint,
    Operand,
)
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedValue, RealizedVector

from .vector_binary import vector_metadata

VECTOR_EACH_PLACEHOLDER = "__runtime_vector_each__"


def realize_vector_scalar(lowerer: Any, value: _VectorScalarOp) -> RealizedVector:
    vector = lowerer.realize_vector(value.vector)
    scalar = lowerer._realize_operand_value(value.scalar)
    scalar_phase = scalar.phase if isinstance(scalar, RealizedValue) else 0
    phase = max(vector.phase, scalar_phase)
    vector = lowerer.delay_vector_to(vector, phase)
    if isinstance(scalar, RealizedValue):
        scalar = lowerer.delay_to(scalar, phase)
        if vector.net != scalar.net:
            lowerer._add_net_conflict(
                vector.net,
                scalar.net,
                "runtime vector data and scalar operand must use opposite wire colors",
            )
    entity = ArithmeticCombinator(
        id=lowerer._take_entity_id(),
        operation=value.op,
        left=Operand(each=True, nets=(vector.net,)),
        right=lowerer._operand(scalar),
        output_each=True,
        description=f"runtime vector {value.op} scalar",
    )
    lowerer.circuit.entities.append(entity)
    endpoint = Endpoint(entity.id, Connector.INPUT)
    lowerer._attach(vector.net, endpoint)
    if isinstance(scalar, RealizedValue):
        lowerer._attach(scalar.net, endpoint)
    fixed, dynamic = vector_metadata(lowerer, vector.net)
    net = lowerer._new_net(
        (),
        Endpoint(entity.id, Connector.OUTPUT),
        label=f"runtime vector {value.op} scalar",
        fixed_signals=fixed,
        carries_dynamic_vector=dynamic,
    )
    return RealizedVector(net, phase + 1)


def realize_vector_filter(lowerer: Any, value: _VectorFilter) -> RealizedVector:
    vector = lowerer.realize_vector(value.vector)
    placeholder = lowerer._new_signal(VECTOR_EACH_PLACEHOLDER)
    entity = DeciderCombinator(
        id=lowerer._take_entity_id(),
        comparator=value.op,
        left=Operand(each=True, nets=(vector.net,)),
        right=Operand(constant=value.right),
        output_signal=placeholder,
        output_copy_count_from_input=True,
        copy_count_nets=(vector.net,),
        description="runtime vector positive filter",
    )
    lowerer.circuit.entities.append(entity)
    lowerer._attach(vector.net, Endpoint(entity.id, Connector.INPUT))
    fixed, dynamic = vector_metadata(lowerer, vector.net)
    net = lowerer._new_net(
        (),
        Endpoint(entity.id, Connector.OUTPUT),
        label="runtime vector positive filter",
        fixed_signals=fixed,
        carries_dynamic_vector=dynamic,
    )
    return RealizedVector(net, vector.phase + 1)
