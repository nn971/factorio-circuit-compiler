"""Clock-derived Event simulation adapters built on the reference Event kernel."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from factorio_circuit.events import EventScheduleError
from factorio_circuit.ir.clocks import EventMerge, GateClock
from factorio_circuit.ir.semantic import (
    CircuitModule,
    ClockContractEnvironment,
    ClockProvenance,
    EventInput,
    PayloadShape,
)
from factorio_circuit.lowering.frontend_to_ir import normalize_module
from factorio_circuit.target.factorio.semantics import i32

from .events import (
    EventOccurrence,
    EventPayload,
    EventSchedule,
    EventSimulationResult,
    SignalMap,
    _dense_level_row,
    _evaluate_scalar,
    _validate_schedules,
)
from .events import simulate_events as _simulate_events

DerivedEvent = GateClock | EventMerge


def _external_event_inputs(module: CircuitModule) -> tuple[EventInput, ...]:
    return tuple(
        source
        for source in module.event_inputs
        if source.clock.provenance is ClockProvenance.EXTERNAL_EVENT
    )


def _merge_payloads(shape: PayloadShape, payloads: Sequence[EventPayload]) -> EventPayload:
    """Add co-present Event payloads using Factorio's signed 32-bit arithmetic."""

    if shape is PayloadShape.SCALAR:
        total = 0
        for payload in payloads:
            if isinstance(payload, bool) or not isinstance(payload, int):
                raise EventScheduleError("scalar EventMerge received a non-scalar parent payload")
            total = i32(total + payload)
        return total

    result: SignalMap = {}
    for payload in payloads:
        if not isinstance(payload, dict):
            raise EventScheduleError("vector EventMerge received a non-vector parent payload")
        for signal, amount in payload.items():
            total = i32(result.get(signal, 0) + amount)
            if total == 0:
                result.pop(signal, None)
            else:
                result[signal] = total
    return result


def _derive_event_schedules(
    module: CircuitModule,
    level_stream: Sequence[Mapping[str, object]],
    schedules: Sequence[EventSchedule],
) -> tuple[EventSchedule, ...]:
    """Validate external schedules and synthesize derived Events in declaration order."""

    if any(isinstance(schedule.source, (GateClock, EventMerge)) for schedule in schedules):
        raise EventScheduleError(
            "derived Event schedules are synthesized from their parents and cannot be supplied "
            "externally"
        )

    environment = ClockContractEnvironment.from_module(module)
    external_inputs = _external_event_inputs(module)
    unsupported = tuple(
        source
        for source in module.event_inputs
        if source.clock.provenance is not ClockProvenance.EXTERNAL_EVENT
        and not isinstance(source, (GateClock, EventMerge))
    )
    if unsupported:
        names = ", ".join(source.name for source in unsupported)
        raise EventScheduleError(f"unsupported derived Event source(s): {names}")

    external_module = replace(module, event_inputs=external_inputs)
    validated = _validate_schedules(external_module, schedules, environment)
    expanded: dict[EventInput, tuple[tuple[EventOccurrence, EventPayload], ...]] = dict(validated)

    for source in module.event_inputs:
        if isinstance(source, GateClock):
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
            continue

        if isinstance(source, EventMerge):
            by_timestamp: dict[int, list[EventPayload]] = {}
            for parent in source.parents:
                parent_occurrences = expanded.get(parent)
                if parent_occurrences is None:
                    raise EventScheduleError(
                        f"EventMerge {source.name!r} parents must be declared before the merge"
                    )
                for occurrence, payload in parent_occurrences:
                    by_timestamp.setdefault(occurrence.timestamp, []).append(payload)
            merged: list[tuple[EventOccurrence, EventPayload]] = []
            for timestamp in sorted(by_timestamp):
                payload = _merge_payloads(source.payload_shape, by_timestamp[timestamp])
                occurrence = EventOccurrence(timestamp, payload)
                merged.append((occurrence, payload))
            expanded[source] = tuple(merged)

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
    """Simulate external Events plus explicit stateless derived Event clocks/merges."""

    normalized = normalize_module(module)
    if not any(isinstance(source, (GateClock, EventMerge)) for source in normalized.event_inputs):
        return _simulate_events(
            normalized,
            level_stream,
            schedules,
            stop_timestamp=stop_timestamp,
        )
    expanded = _derive_event_schedules(normalized, level_stream, schedules)
    return _simulate_events(
        normalized,
        level_stream,
        expanded,
        stop_timestamp=stop_timestamp,
    )
