"""Deterministic Event output-materialization cases for differential tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from random import Random

from factorio_circuit import Circuit, EventOccurrence, EventSchedule, ScalarEvent
from factorio_circuit.ir.output import OutputMaterializationPolicy
from factorio_circuit.target.factorio.semantics import i32

_EVENT_SEPARATION = 4
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
class OutputMaterializationCase:
    seed: int
    scale: int
    bias: int
    occurrences: tuple[ScalarCaseOccurrence, ...]

    def describe(self) -> str:
        return "\n".join(
            (
                f"seed={self.seed} scale={self.scale} bias={self.bias}",
                "occurrences=" + repr(self.occurrences),
            )
        )


@dataclass(frozen=True, slots=True)
class OutputMaterializationCircuit:
    circuit: Circuit
    source: ScalarEvent


def generate_output_materialization_case(seed: int) -> OutputMaterializationCase:
    """Generate an irregular scalar Event trace with one guaranteed zero-payload occurrence."""

    rng = Random(seed)
    occurrences: list[ScalarCaseOccurrence] = []
    for timestamp in _CANDIDATE_TIMESTAMPS:
        if timestamp == 4:
            payload = _random_nonzero_i32(rng)
        elif timestamp == 8:
            payload = 0
        elif rng.random() < 0.55:
            payload = _random_i32(rng)
        else:
            continue
        occurrences.append(ScalarCaseOccurrence(timestamp, payload))

    return OutputMaterializationCase(
        seed=seed,
        scale=rng.choice(_SCALES),
        bias=rng.choice(_BIASES),
        occurrences=tuple(occurrences),
    )


def build_output_materialization_circuit(
    case: OutputMaterializationCase,
    policy: OutputMaterializationPolicy,
) -> OutputMaterializationCircuit:
    """Build one transformed scalar Event with an explicit output policy."""

    circuit = Circuit(f"random_{policy.value}_output_{case.seed}")
    source = circuit.event("source", guaranteed_min_separation=_EVENT_SEPARATION)
    value = source * case.scale + case.bias
    circuit.output("value", value, policy=policy)
    return OutputMaterializationCircuit(circuit, source)


def event_schedule(
    case: OutputMaterializationCase,
    built: OutputMaterializationCircuit,
) -> EventSchedule:
    return EventSchedule(
        built.source,
        tuple(
            EventOccurrence(occurrence.timestamp, occurrence.payload)
            for occurrence in case.occurrences
        ),
    )


def physical_rows(case: OutputMaterializationCase) -> list[dict[str, object]]:
    occurrences = {item.timestamp: item.payload for item in case.occurrences}
    return [
        {
            "source": occurrences.get(timestamp, 0),
            "source__valid": int(timestamp in occurrences),
        }
        for timestamp in range(_HORIZON)
    ]


def shrink_output_materialization_case(
    case: OutputMaterializationCase,
    fails: Callable[[OutputMaterializationCase], bool],
) -> OutputMaterializationCase:
    """Reduce Event occurrences and simplify the payload transform."""

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

    return current


def _random_i32(rng: Random) -> int:
    if rng.random() < 0.55:
        return rng.choice(_INTERESTING_VALUES)
    return i32(rng.getrandbits(32))


def _random_nonzero_i32(rng: Random) -> int:
    value = _random_i32(rng)
    return 1 if value == 0 else value
