"""Lower runtime-open vector selection."""

from __future__ import annotations

from typing import Any

from factorio_circuit.frontend import _VectorSelect
from factorio_circuit.ir.abstract_physical import ArithmeticCombinator, Connector, Endpoint, Operand
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedVector

from .vector_binary import vector_metadata


def realize_vector_select(lowerer: Any, value: _VectorSelect) -> RealizedVector:
    source = lowerer.realize_vector(value.vector)
    entity = ArithmeticCombinator(
        id=lowerer._take_entity_id(),
        operation="select",
        left=Operand(each=True, nets=(source.net,)),
        right=Operand(constant=value.index),
        output_each=True,
        description="runtime vector max" if value.select_max else "runtime vector select",
    )
    lowerer.circuit.entities.append(entity)
    lowerer._attach(source.net, Endpoint(entity.id, Connector.INPUT))
    fixed, dynamic = vector_metadata(lowerer, source.net)
    net = lowerer._new_net(
        (),
        Endpoint(entity.id, Connector.OUTPUT),
        label="runtime vector selection",
        fixed_signals=fixed,
        carries_dynamic_vector=dynamic,
    )
    return RealizedVector(net, source.phase + 1)
