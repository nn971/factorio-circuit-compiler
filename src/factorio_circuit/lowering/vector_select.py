"""Lower runtime-open vector selection."""

from __future__ import annotations

from typing import Any

from factorio_circuit.analysis.latency import FACTORIO_LATENCY
from factorio_circuit.ir.abstract_physical import Connector, Endpoint, SelectorCombinator
from factorio_circuit.ir.semantic import VectorSelect
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedVector

from .vector_binary import vector_metadata


def _selector_description(value: VectorSelect) -> str:
    if value.index == 0:
        return "runtime vector max" if value.select_max else "runtime vector min"
    order = "descending" if value.select_max else "ascending"
    return f"runtime vector select index {value.index} {order}"


def realize_vector_select(lowerer: Any, value: VectorSelect) -> RealizedVector:
    source = lowerer.realize_vector(value.vector)
    phase = source.phase
    schedule = getattr(lowerer, "_operation_input_phase", None)
    if schedule is not None:
        phase = schedule(value, "vector_select", value.op, phase)
    source = lowerer.delay_vector_to(source, phase)
    entity = SelectorCombinator(
        id=lowerer._take_entity_id(),
        operation="select",
        input_nets=(source.net,),
        select_max=value.select_max,
        index=value.index,
        description=_selector_description(value),
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
    return RealizedVector(
        net, phase + FACTORIO_LATENCY.operation_latency("vector_select", value.op)
    )
