"""Public API for factorio-circuit-compiler."""

from .analysis.state_timing import ClockDomainTiming, StateTimingError, StateTimingPlan
from .compiler import CompilationResult, compile_circuit
from .events import (
    EventCausalityError,
    EventCompilationError,
    EventCrossingError,
    EventMaterializationError,
    EventScheduleError,
    EventThroughputError,
)
from .frontend import (
    AccumulatorReg,
    Circuit,
    CircuitBuildError,
    Expr,
    FreezeReg,
    Input,
    LogicalTime,
    SampleOnReference,
    ScalarEvent,
    SignalsExpr,
    SignalsInput,
    VectorEvent,
)
from .ir.output import (
    MaterializedReturnValue,
    OutputMaterialization,
    OutputMaterializationPolicy,
    output_materializations,
)
from .ir.physical import SignalId
from .simulate.clocked_events import simulate_events
from .simulate.events import (
    EventActivation,
    EventMaterializationPolicy,
    EventOccurrence,
    EventReaction,
    EventSchedule,
    EventSimulationResult,
    MaterializedEventTrace,
    SampleOnObservation,
    TimestampDomain,
    materialize_event_trace,
)
from .simulate.output_materialization import MaterializedOutputTrace, materialize_output_trace
from .synthesis.placement import PlacementMetrics, PlacementOptions, placement_metrics

__all__ = [
    "AccumulatorReg",
    "Circuit",
    "CircuitBuildError",
    "ClockDomainTiming",
    "CompilationResult",
    "Expr",
    "EventCausalityError",
    "EventActivation",
    "EventCompilationError",
    "EventCrossingError",
    "EventOccurrence",
    "EventReaction",
    "EventSchedule",
    "EventScheduleError",
    "EventSimulationResult",
    "EventMaterializationError",
    "EventMaterializationPolicy",
    "EventThroughputError",
    "FreezeReg",
    "Input",
    "LogicalTime",
    "MaterializedEventTrace",
    "MaterializedOutputTrace",
    "MaterializedReturnValue",
    "OutputMaterialization",
    "OutputMaterializationPolicy",
    "PlacementMetrics",
    "PlacementOptions",
    "SignalsExpr",
    "SignalsInput",
    "SampleOnObservation",
    "SampleOnReference",
    "ScalarEvent",
    "SignalId",
    "StateTimingError",
    "StateTimingPlan",
    "TimestampDomain",
    "VectorEvent",
    "compile_circuit",
    "materialize_event_trace",
    "materialize_output_trace",
    "output_materializations",
    "placement_metrics",
    "simulate_events",
]
