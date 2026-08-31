"""Deterministic Event crossing/derived-clock cases for differential tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from random import Random

from factorio_circuit import Circuit, EventOccurrence, EventSchedule, ScalarEvent
from factorio_circuit.ir.output import OutputMaterializationPolicy
from factorio_circuit.target.factorio.semantics import i32

_PARENT_SEPARATION = 4
_HORIZON = 17
_CANDIDATE_TIMESTAMPS = (0, 4, 8, 12, 16)
_INTERESTING_VALUES = (0, 1, -1, 2, -2, 7, 31, 2**31 - 1, -(2**31))
_SCALES = (-2, -1, 1, 2)
_BIASES = (-2, -1, 0, 1, 2)


@dataclass(frozen=True, slots=True)
class ScalarCaseOccurrence:
    timestamp: int
    payload: int


@dataclass(frozen=True, slots=True)
class CrossingCase:
    """One merge -> gate -> Level sample case on a finite timestamp horizon."""

    seed: int
    scale: int
    bias: int
    enabled: tuple[int, ...]
    data: tuple[int, ...]
    left: tuple[ScalarCaseOccurrence, ...]
    right: tuple[ScalarCaseOccurrence, ...]

    def describe(self) -> str:
        lines = [f"seed={self.seed} scale={self.scale} bias={self.bias}"]
        lines.append("enabled=" + repr(self.enabled))
        lines.append("data=" + repr(self.data))
        lines.append("left=" + repr(self.left))
        lines.append("right=" + repr(self.right))
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class CrossingCircuit:
    circuit: Circuit
    left: ScalarEvent
    right: ScalarEvent


def generate_crossing_case(seed: int) -> CrossingCase:
    """Generate separated parent Events with one guaranteed simultaneous cancelling occurrence."""

    rng = Random(seed)
    enabled = [rng.randrange(2) for _ in range(_HORIZON)]
    data = [_random_i32(rng) for _ in range(_HORIZON)]

    left: list[ScalarCaseOccurrence] = []
    right: list[ScalarCaseOccurrence] = []
    for timestamp in _CANDIDATE_TIMESTAMPS:
        if timestamp == 8:
            value = rng.choice((1, 2, 7, 31))
            left.append(ScalarCaseOccurrence(timestamp, value))
            right.append(ScalarCaseOccurrence(timestamp, -value))
            enabled[timestamp] = 1
            continue
        if rng.random() < 0.55:
            left.append(ScalarCaseOccurrence(timestamp, _random_i32(rng)))
        if rng.random() < 0.55:
            right.append(ScalarCaseOccurrence(timestamp, _random_i32(rng)))

    return CrossingCase(
        seed=seed,
        scale=rng.choice(_SCALES),
        bias=rng.choice(_BIASES),
        enabled=tuple(enabled),
        data=tuple(data),
        left=tuple(left),
        right=tuple(right),
    )


def build_crossing_circuit(case: CrossingCase) -> CrossingCircuit:
    """Build the public frontend chain EventMerge -> GateClock -> SampleOn -> VALID output."""

    circuit = Circuit(f"random_crossing_{case.seed}")
    enabled = circuit.input("enabled")
    data = circuit.input("data")
    left = circuit.event("left", guaranteed_min_separation=_PARENT_SEPARATION)
    right = circuit.event("right", guaranteed_min_separation=_PARENT_SEPARATION)
    merged = circuit.event_merge(left, right)
    gated = circuit.gate_clock(merged, when=circuit.sample_on(enabled, merged))
    sampled = circuit.sample_on(data, gated)
    value = sampled * case.scale + case.bias
    circuit.output("sampled", value, policy=OutputMaterializationPolicy.VALID)
    return CrossingCircuit(circuit, left, right)


def level_stream(case: CrossingCase) -> list[dict[str, object]]:
    return [
        {"enabled": case.enabled[timestamp], "data": case.data[timestamp]}
        for timestamp in range(_HORIZON)
    ]


def event_schedules(case: CrossingCase, built: CrossingCircuit) -> tuple[EventSchedule, ...]:
    return (
        EventSchedule(
            built.left,
            tuple(EventOccurrence(item.timestamp, item.payload) for item in case.left),
        ),
        EventSchedule(
            built.right,
            tuple(EventOccurrence(item.timestamp, item.payload) for item in case.right),
        ),
    )


def physical_rows(case: CrossingCase) -> list[dict[str, object]]:
    left = {item.timestamp: item.payload for item in case.left}
    right = {item.timestamp: item.payload for item in case.right}
    rows: list[dict[str, object]] = []
    for timestamp in range(_HORIZON):
        rows.append(
            {
                "enabled": case.enabled[timestamp],
                "data": case.data[timestamp],
                "left": left.get(timestamp, 0),
                "left__valid": int(timestamp in left),
                "right": right.get(timestamp, 0),
                "right__valid": int(timestamp in right),
            }
        )
    return rows


def shrink_crossing_case(
    case: CrossingCase,
    fails: Callable[[CrossingCase], bool],
) -> CrossingCase:
    """Reduce parent schedules, output arithmetic, and sampled Level rows."""

    current = case
    changed = True
    while changed:
        changed = False
        for side in ("left", "right"):
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

        if current.scale != 1:
            candidate = replace(current, scale=1)
            if fails(candidate):
                current = candidate
                changed = True
                continue
        if current.bias != 0:
            candidate = replace(current, bias=0)
            if fails(candidate):
                current = candidate
                changed = True
                continue

        for timestamp, value in enumerate(current.enabled):
            if value == 0:
                continue
            enabled = list(current.enabled)
            enabled[timestamp] = 0
            candidate = replace(current, enabled=tuple(enabled))
            if fails(candidate):
                current = candidate
                changed = True
                break
        if changed:
            continue

        for timestamp, value in enumerate(current.data):
            if value == 0:
                continue
            data = list(current.data)
            data[timestamp] = 0
            candidate = replace(current, data=tuple(data))
            if fails(candidate):
                current = candidate
                changed = True
                break

    return current


def _random_i32(rng: Random) -> int:
    if rng.random() < 0.45:
        return rng.choice(_INTERESTING_VALUES)
    return i32(rng.getrandbits(32))
