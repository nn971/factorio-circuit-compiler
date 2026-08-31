"""Deterministic external-Event state cases for compiler differential tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from random import Random

from factorio_circuit import Circuit, SignalId, VectorEvent
from factorio_circuit.simulate.events import EventOccurrence, EventSchedule
from factorio_circuit.target.factorio.semantics import i32

_EVENT_SEPARATION = 8
_SIGNALS = (
    SignalId("item", "iron-plate"),
    SignalId("item", "copper-plate"),
    SignalId("item", "coal"),
    SignalId("virtual", "signal-Q"),
)
_INTERESTING_VALUES = (0, 1, -1, 2, -2, 7, 31, 2**31 - 1, -(2**31))
_SCALES = (-2, -1, 1, 2)


@dataclass(frozen=True, slots=True)
class EventCaseOccurrence:
    timestamp: int
    payload: tuple[tuple[SignalId, int], ...]

    def signal_map(self) -> dict[SignalId, int]:
        return dict(self.payload)


@dataclass(frozen=True, slots=True)
class EventStateCase:
    seed: int
    scale: int
    positive_only: bool
    occurrences: tuple[EventCaseOccurrence, ...]

    def describe(self) -> str:
        lines = [f"seed={self.seed} scale={self.scale} positive_only={self.positive_only}"]
        for occurrence in self.occurrences:
            rendered = ", ".join(
                f"{signal.kind}:{signal.name}={value}" for signal, value in occurrence.payload
            )
            lines.append(f"t={occurrence.timestamp}: {{{rendered}}}")
        return "\n".join(lines)


def generate_event_state_case(
    seed: int,
    *,
    min_occurrences: int = 4,
    max_occurrences: int = 7,
) -> EventStateCase:
    """Generate one separated external vector-Event trace and a per-occurrence transform."""

    rng = Random(seed)
    timestamp = rng.randrange(3)
    occurrences: list[EventCaseOccurrence] = []
    for _ in range(rng.randint(min_occurrences, max_occurrences)):
        payload: list[tuple[SignalId, int]] = []
        for signal in _SIGNALS:
            if rng.random() >= 0.55:
                continue
            value = _random_i32(rng)
            if value != 0:
                payload.append((signal, value))
        payload.sort(key=lambda item: (item[0].kind, item[0].name))
        occurrences.append(EventCaseOccurrence(timestamp, tuple(payload)))
        timestamp += _EVENT_SEPARATION + rng.randrange(3)

    return EventStateCase(
        seed=seed,
        scale=rng.choice(_SCALES),
        positive_only=bool(rng.getrandbits(1)),
        occurrences=tuple(occurrences),
    )


def build_event_state_circuit(case: EventStateCase) -> tuple[Circuit, VectorEvent]:
    """Build a direct Event accumulator entirely through the public frontend."""

    circuit = Circuit(f"random_event_state_{case.seed}")
    source = circuit.signal_event(
        "source",
        guaranteed_min_separation=_EVENT_SEPARATION,
    )
    value = source * case.scale
    if case.positive_only:
        value = value.positive()
    total = circuit.accumulator("total")
    total.add(value)
    circuit.output("total", total.sample())
    return circuit, source


def event_schedule(case: EventStateCase, source: VectorEvent) -> EventSchedule:
    return EventSchedule(
        source,
        tuple(
            EventOccurrence(occurrence.timestamp, occurrence.signal_map())
            for occurrence in case.occurrences
        ),
    )


def physical_event_rows(case: EventStateCase) -> list[dict[str, object]]:
    """Encode Event presence through the compiler's payload + ``__valid`` physical ABI."""

    stop = case.occurrences[-1].timestamp + 1 if case.occurrences else 1
    rows: list[dict[str, object]] = [{"source": {}, "source__valid": 0} for _ in range(stop)]
    for occurrence in case.occurrences:
        rows[occurrence.timestamp] = {
            "source": occurrence.signal_map(),
            "source__valid": 1,
        }
    return rows


def shrink_event_state_case(
    case: EventStateCase,
    fails: Callable[[EventStateCase], bool],
) -> EventStateCase:
    """Greedily reduce Event occurrences and then simplify the payload transform."""

    current = case
    changed = True
    while changed:
        changed = False
        if len(current.occurrences) > 1:
            for index in range(len(current.occurrences)):
                candidate = replace(
                    current,
                    occurrences=current.occurrences[:index] + current.occurrences[index + 1 :],
                )
                if fails(candidate):
                    current = candidate
                    changed = True
                    break
            if changed:
                continue

        if current.positive_only:
            candidate = replace(current, positive_only=False)
            if fails(candidate):
                current = candidate
                changed = True
                continue

        if current.scale != 1:
            candidate = replace(current, scale=1)
            if fails(candidate):
                current = candidate
                changed = True
                continue

    return current


def _random_i32(rng: Random) -> int:
    if rng.random() < 0.45:
        return rng.choice(_INTERESTING_VALUES)
    return i32(rng.getrandbits(32))
