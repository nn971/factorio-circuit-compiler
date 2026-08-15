"""Clock-derived Event simulation adapters built on the reference Event kernel."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from factorio_circuit.events import EventScheduleError
from factorio_circuit.ir.clocks import GateClock
from factorio_circuit.ir.semantic import (
    CircuitModule,
    ClockContractEnvironment,
    ClockProvenance,
    EventInput,
)
from factorio_circuit.lowering.frontend_to_ir import normalize_module

from .events import (
    EventOccurrence,
    EventPayload,
    EventSchedule,
    EventSimulationResult,
    _dense_level_row,
    _evaluate_scalar,
    _validate_schedules,
)
from .events import simulate_events as _simulate_events


def _external_event_inputs(module: CircuitModule) -> tuple[EventInput, ...]:
    return tuple(
        source
        for source in module.event_inputs
        if source.clock.provenance is ClockProvenance.EXTERNAL_EVENT
    )


def _gate_schedules(
    module: CircuitModule,
    level_stream: Sequence[Mapping[str, object]],
    schedules: Sequence[EventSchedule],
) -> tuple[EventSchedule, ...]:
    """Validate external schedules and synthesize every declared GateClock in declaration order."""

    if any(isinstance(schedule.source, GateClock) for schedule in schedules):
        raise EventScheduleError(
            "GateClock schedules are derived from their parent and cannot be supplied externally"
        )

    environment = ClockContractEnvironment.from_module(module)
    external_inputs = _external_event_inputs(module)
    unsupported = tuple(
        source
        for source in module.event_inputs
        if source.clock.provenance is not ClockProvenance.EXTERNAL_EVENT
        and not isinstance(source, GateClock)
    )
    if unsupported:
        names = ", ".join(source.name for source in unsupported)
        raise EventScheduleError(f"unsupported derived Event source(s): {names}")

    external_module = replace(module, event_inputs=external_inputs)
    validated = _validate_schedules(external_module, schedules, environment)
    expanded: dict[EventInput, tuple[tuple[EventOccurrence, EventPayload], ...]] = dict(validated)

    for source in module.event_inputs:
        if not isinstance(source, GateClock):
            continue
        parent_occurrences = expanded.get(source.parent)
        if parent_occurrences is None:
            raise EventScheduleError(
                f"GateClock {source.name!r} parent must be declared before the derived clock"
            )
        derived: list[tuple[EventOccurrence, EventPayload]] = []
        for occurrence, payload in parent_occurrences:
            level_row = _dense_level_row(module, level_stream, occurrence.timestamp)
            enabled = _evaluate_scalar(
                source.predicate,
                level_row,
                {},
                source.parent,
                payload,
            )
            if enabled != 0:
                gated_occurrence = EventOccurrence(occurrence.timestamp, 1)
                derived.append((gated_occurrence, 1))
        expanded[source] = tuple(derived)

    return tuple(
        EventSchedule(
            source,
            tuple(
                EventOccurrence(occurrence.timestamp, payload)
                for occurrence, payload in expanded[source]
            ),
        )
        for source in module.event_inputs
    )


def simulate_events(
    module: CircuitModule,
    level_stream: Sequence[Mapping[str, object]],
    schedules: Sequence[EventSchedule],
    *,
    stop_timestamp: int | None = None,
) -> EventSimulationResult:
    """Simulate external Events plus explicit derived clocks such as :class:`GateClock`."""

    normalized = normalize_module(module)
    if not any(isinstance(source, GateClock) for source in normalized.event_inputs):
        return _simulate_events(
            normalized,
            level_stream,
            schedules,
            stop_timestamp=stop_timestamp,
        )
    expanded = _gate_schedules(normalized, level_stream, schedules)
    return _simulate_events(
        normalized,
        level_stream,
        expanded,
        stop_timestamp=stop_timestamp,
    )
