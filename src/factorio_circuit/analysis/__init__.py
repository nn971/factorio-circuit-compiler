"""Compiler analyses."""

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
    "ClockDomainTiming",
    "FACTORIO_LATENCY",
    "EventClockTiming",
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
    "validate_event_throughput",
]
