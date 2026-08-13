"""Whole-vector lowering pipeline."""

from typing import cast

from factorio_circuit.analysis.state_timing import StateTimingPlan
from factorio_circuit.frontend import _VectorBinaryOp, _VectorFilter, _VectorScalarOp
from factorio_circuit.ir.abstract_physical import AbstractNet, AbstractPhysicalCircuit
from factorio_circuit.ir.semantic import (
    CircuitModule,
    VectorConstant,
    VectorInput,
    VectorInputSample,
    VectorValue,
)
from factorio_circuit.ir.state import VectorRegisterRead
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedValue, RealizedVector

from .open_vector import VectorLowerer

_VECTOR_OUTPUTS = (
    VectorInput,
    VectorInputSample,
    VectorConstant,
    VectorRegisterRead,
    _VectorBinaryOp,
    _VectorScalarOp,
    _VectorFilter,
)


def lower_stateless_vectors(
    module: CircuitModule,
    *,
    enable_packing: bool,
    state_timing: StateTimingPlan,
) -> AbstractPhysicalCircuit:
    lowerer = VectorLowerer(
        module,
        enable_packing=enable_packing,
        state_timing=state_timing,
    )
    lowerer._create_input_markers()
    outputs: list[RealizedValue | RealizedVector] = []
    for value in module.output.values:
        if isinstance(value, _VECTOR_OUTPUTS):
            outputs.append(lowerer.realize_vector(cast(VectorValue, value)))
        else:
            outputs.append(lowerer.realize(value))
    lowerer._create_output_markers(outputs)
    lowerer.circuit.nets = [
        AbstractNet(
            id=net_id,
            signals=builder.signals,
            endpoints=tuple(builder.endpoints),
            label=builder.label,
            fixed_signals=builder.fixed_signals,
            carries_dynamic_vector=builder.carries_dynamic_vector,
        )
        for net_id, builder in sorted(lowerer.net_builders.items())
    ]
    lowerer.circuit.validate()
    return lowerer.circuit
