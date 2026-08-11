"""Public API for factorio-circuit-compiler."""

from .analysis.state_timing import StateTimingError, StateTimingPlan
from .compiler import CompilationResult, compile_circuit
from .frontend.symbolic import (
    AccumulatorReg,
    Circuit,
    CircuitBuildError,
    Expr,
    FreezeReg,
    Input,
    LogicalTime,
    SignalsExpr,
    SignalsInput,
)
from .ir.physical import SignalId

__all__ = [
    "AccumulatorReg",
    "Circuit",
    "CircuitBuildError",
    "CompilationResult",
    "Expr",
    "FreezeReg",
    "Input",
    "LogicalTime",
    "SignalsExpr",
    "SignalsInput",
    "SignalId",
    "StateTimingError",
    "StateTimingPlan",
    "compile_circuit",
]
