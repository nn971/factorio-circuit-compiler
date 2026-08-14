"""Public API for factorio-circuit-compiler."""

from .analysis.state_timing import ClockDomainTiming, StateTimingError, StateTimingPlan
from .compiler import (
    AbstractCompilationResult,
    CompilationResult,
    compile_abstract_circuit,
    compile_circuit,
)
from .frontend import (
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
from .synthesis.placement import PlacementMetrics, PlacementOptions, placement_metrics

__all__ = [
    "AbstractCompilationResult",
    "AccumulatorReg",
    "Circuit",
    "CircuitBuildError",
    "ClockDomainTiming",
    "CompilationResult",
    "Expr",
    "FreezeReg",
    "Input",
    "LogicalTime",
    "PlacementMetrics",
    "PlacementOptions",
    "SignalsExpr",
    "SignalsInput",
    "SignalId",
    "StateTimingError",
    "StateTimingPlan",
    "compile_abstract_circuit",
    "compile_circuit",
    "placement_metrics",
]
