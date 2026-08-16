"""Public symbolic frontend, including runtime-open vector expressions."""

from factorio_circuit.ir.semantic import (
    VectorBinaryOp,
    VectorFilter,
    VectorScalarOp,
    VectorSelect,
)

from .clock_bridges import Circuit
from .symbolic import CircuitBuildError, LogicalTime
from .vector_circuit import (
    AccumulatorReg,
    Expr,
    FreezeReg,
    Input,
    SampleOnReference,
    ScalarEvent,
    SignalsInput,
    VectorEvent,
)
from .vector_expr import SignalsExpr

__all__ = [
    "AccumulatorReg",
    "Circuit",
    "CircuitBuildError",
    "Expr",
    "FreezeReg",
    "Input",
    "LogicalTime",
    "SampleOnReference",
    "ScalarEvent",
    "SignalsExpr",
    "SignalsInput",
    "VectorBinaryOp",
    "VectorEvent",
    "VectorFilter",
    "VectorScalarOp",
    "VectorSelect",
]
