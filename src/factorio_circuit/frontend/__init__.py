"""Public symbolic frontend, including runtime-open vectors and semantic oracles."""

from factorio_circuit.ir.semantic import (
    VectorBinaryOp,
    VectorFilter,
    VectorScalarOp,
    VectorSelect,
)

from .oracle import Circuit, Oracle, SignalsOracle
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
    "Oracle",
    "SampleOnReference",
    "ScalarEvent",
    "SignalsExpr",
    "SignalsInput",
    "SignalsOracle",
    "VectorBinaryOp",
    "VectorEvent",
    "VectorFilter",
    "VectorScalarOp",
    "VectorSelect",
]
