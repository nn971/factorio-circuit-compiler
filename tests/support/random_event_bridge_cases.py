"""Deterministic SumInto/HoldInto cases for compiler differential tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from random import Random

from factorio_circuit import (
    Circuit,
    EventOccurrence,
    EventSchedule,
    ScalarEvent,
    SignalId,
    VectorEvent,
)
from factorio_circuit.ir.output import OutputMaterializationPolicy
from factorio_circuit.target.factorio.semantics import i32

_EVENT_SEPARATION = 4
_HORIZON = 17
_CANDIDATE_TIMESTAMPS = (0, 4, 8, 12, 16)
_SIGNALS = (
    SignalId("item", "iron-plate"),
    SignalId("item", "copper-plate"),
    SignalId("item", "coal"),
    SignalId("virtual", "signal-Q"),
)
_INTERESTING_VALUES = (1, -1, 2, -2, 7, 31, 2**31 - 1, -(2**31))


@dataclass(frozen=True, slots=True)
class VectorCaseOccurrence:
    timestamp: int
    payload: tuple[tuple[SignalId, int], ...]

    def signal_map(self) -> dict[SignalId, int]:
        return dict(self.payload)


@dataclass(frozen=True, slots=True)
class ScalarCaseOccurrence:
    timestamp: int
    payload: int


@dataclass(frozen=True, slots=True)
class EventBridgeCase:
    seed: int
    source: tuple[VectorCaseOccurrence, ...]
    target: tuple[ScalarCaseOccurrence, ...]

    def describe(self) -> str:
        return "\n".join(
            (
                f"seed={self.seed}",
                "source=" + repr(self.source),
                "target=" + repr(self.target),
            )
        )


@dataclass(frozen=True, slots=True)
class EventBridgeCircuit:
    circuit: Circuit
    source: VectorEvent
    target: ScalarEvent


def generate_event_bridge_case(seed: int) -> EventBridgeCase:
    """Generate a shared source/target trace with initial, collision, and later targets."""

    rng = Random(seed)
    source: list[VectorCaseOccurrence] = []
    target: list[ScalarCaseOccurrence] = []

    for timestamp in _CANDIDATE_TIMESTAMPS:
        if timestamp in {4, 8} or (timestamp > 8 and rng.random() < 0.55):
            source.append(
                VectorCaseOccurrence(
                    timestamp,
                    _random_payload(rng, force_nonempty=timestamp in {4, 8}),
                )
            )
        if timestamp in {0, 8, 16} or (timestamp in {4, 12} and rng.random() < 0.45):
            target.append(ScalarCaseOccurrence(timestamp, _random_i32(rng)))

    return EventBridgeCase(seed=seed, source=tuple(source), target=tuple(target))


def build_event_bridge_circuit(case: EventBridgeCase, kind: str) -> EventBridgeCircuit:
    """Build one direct cross-clock vector bridge through the public frontend."""

    circuit = Circuit(f"random_{kind}_bridge_{case.seed}")
    source = circuit.signal_event("source", guaranteed_min_separation=_EVENT_SEPARATION)
    target = circuit.event("target", guaranteed_min_separation=_EVENT_SEPARATION)
    if kind == "sum":
        bridged = circuit.sum_into(source, target)
    elif kind == "hold":
        bridged = circuit.hold_into(source, target)
    else:
        raise ValueError(f"unsupported event bridge kind: {kind!r}")
    circuit.output("bridge", bridged, policy=OutputMaterializationPolicy.VALID)
    return EventBridgeCircuit(circuit, source, target)


def event_schedules(
    case: EventBridgeCase,
    built: EventBridgeCircuit,
) -> tuple[EventSchedule, ...]:
    return (
        EventSchedule(
            built.source,
            tuple(
                EventOccurrence(occurrence.timestamp, occurrence.signal_map())
                for occurrence in case.source
            ),
        ),
        EventSchedule(
            built.target,
            tuple(
                EventOccurrence(occurrence.timestamp, occurrence.payload)
                for occurrence in case.target
            ),
        ),
    )


def physical_rows(case: EventBridgeCase) -> list[dict[str, object]]:
    source = {occurrence.timestamp: occurrence.signal_map() for occurrence in case.source}
    target = {occurrence.timestamp: occurrence.payload for occurrence in case.target}
    rows: list[dict[str, object]] = []
    for timestamp in range(_HORIZON):
        rows.append(
            {
                "source": source.get(timestamp, {}),
                "source__valid": int(timestamp in source),
                "target": target.get(timestamp, 0),
                "target__valid": int(timestamp in target),
            }
        )
    return rows


def shrink_event_bridge_case(
    case: EventBridgeCase,
    fails: Callable[[EventBridgeCase], bool],
) -> EventBridgeCase:
    """Reduce source/target schedules and then remove individual source lanes."""

    current = case
    changed = True
    while changed:
        changed = False
        for side in ("source", "target"):
            occurrences = getattr(current, side)
            for index in range(len(occurrences)):
                candidate = replace(
                    current,
                    **{side: occurrences[:index] + occurrences[index + 1 :]},
                )
                if fails(candidate):
                    current = candidate
                    changed = True
                    break
            if changed:
                break
        if changed:
            continue

        for occurrence_index, occurrence in enumerate(current.source):
            for lane_index in range(len(occurrence.payload)):
                payload = occurrence.payload[:lane_index] + occurrence.payload[lane_index + 1 :]
                source = list(current.source)
                source[occurrence_index] = replace(occurrence, payload=payload)
                candidate = replace(current, source=tuple(source))
                if fails(candidate):
                    current = candidate
                    changed = True
                    break
            if changed:
                break

    return current


def _random_payload(
    rng: Random,
    *,
    force_nonempty: bool,
) -> tuple[tuple[SignalId, int], ...]:
    payload: list[tuple[SignalId, int]] = []
    for signal in _SIGNALS:
        if rng.random() < 0.5:
            payload.append((signal, _random_i32(rng)))
    if force_nonempty and not payload:
        payload.append((rng.choice(_SIGNALS), _random_i32(rng)))
    payload.sort(key=lambda item: (item[0].kind, item[0].name))
    return tuple(payload)


def _random_i32(rng: Random) -> int:
    if rng.random() < 0.55:
        return rng.choice(_INTERESTING_VALUES)
    value = i32(rng.getrandbits(32))
    return 1 if value == 0 else value
