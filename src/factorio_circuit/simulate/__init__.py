"""Reference simulation entry points."""

from factorio_circuit.events import (
    EventCausalityError,
    EventCrossingError,
    EventMaterializationError,
)

from .clocked_events import simulate_events
from .events import (
    EventActivation,
    EventMaterializationPolicy,
    EventOccurrence,
    EventReaction,
    EventSchedule,
    EventScheduleError,
    EventSimulationResult,
    EventThroughputError,
    MaterializedEventTrace,
    SampleOnObservation,
    TimestampDomain,
    materialize_event_trace,
)
from .oracle import simulate_stream_with_oracles

__all__ = [
    "EventActivation",
    "EventOccurrence",
    "EventCausalityError",
    "EventCrossingError",
    "EventReaction",
    "EventSchedule",
    "EventScheduleError",
    "EventSimulationResult",
    "EventThroughputError",
    "EventMaterializationError",
    "EventMaterializationPolicy",
    "MaterializedEventTrace",
    "SampleOnObservation",
    "TimestampDomain",
    "materialize_event_trace",
    "simulate_events",
    "simulate_stream_with_oracles",
]
