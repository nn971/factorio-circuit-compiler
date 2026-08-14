"""Reference simulation entry points."""

from factorio_circuit.events import (
    EventCausalityError,
    EventCrossingError,
    EventMaterializationError,
)

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
    simulate_events,
)

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
]
