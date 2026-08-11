"""Public API for factorio-circuit-compiler."""

from .analysis.state_timing import StateTimingError, StateTimingPlan
from .compiler import (
    AbstractCompilationResult,
    CompilationResult,
    compile_abstract_circuit,
    compile_circuit,
)
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
    "AbstractCompilationResult",
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
    "compile_abstract_circuit",
    "compile_circuit",
]
