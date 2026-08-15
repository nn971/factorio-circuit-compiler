"""Compiler analyses."""

from .causality import (
    CausalityEdge,
    CausalityEdgeKind,
    CausalityGraph,
    LogicalDependency,
    has_nonpositive_cycle,
)
from .latency import FACTORIO_LATENCY, TargetLatencyModel
from .state_timing import (
    ClockDomainTiming,
    EventClockTiming,
    RegisterTiming,
    StateReadTiming,
    StateTimingError,
    StateTimingPlan,
    UnsupportedClockCrossing,
    analyze_clocked_timing,
    analyze_normalized_state_timing,
    analyze_state_timing,
    earliest_scalar_phase,
    earliest_vector_phase,
    validate_event_throughput,
)

__all__ = [
    "CausalityEdge",
    "CausalityEdgeKind",
    "CausalityGraph",
    "ClockDomainTiming",
    "FACTORIO_LATENCY",
    "EventClockTiming",
    "LogicalDependency",
    "RegisterTiming",
    "StateReadTiming",
    "StateTimingError",
    "StateTimingPlan",
    "TargetLatencyModel",
    "UnsupportedClockCrossing",
    "analyze_normalized_state_timing",
    "analyze_clocked_timing",
    "analyze_state_timing",
    "earliest_scalar_phase",
    "earliest_vector_phase",
    "has_nonpositive_cycle",
    "validate_event_throughput",
]
