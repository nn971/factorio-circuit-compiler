from __future__ import annotations

from dataclasses import replace

import pytest

from factorio_circuit import SignalId, simulate_events
from factorio_circuit.simulate.physical import simulate_stream
from tests.support.random_event_state_cases import (
    EventCaseOccurrence,
    EventStateCase,
    build_event_state_circuit,
    event_schedule,
    generate_event_state_case,
    physical_event_rows,
    shrink_event_state_case,
)

_PROGRAM_SEEDS = (0xE7E17, 0xCA57, 0x5EED4)
IRON = SignalId("item", "iron-plate")
COPPER = SignalId("item", "copper-plate")


def _assert_event_state_case_equivalent(case: EventStateCase, *, optimize: bool) -> None:
    circuit, source = build_event_state_circuit(case)
    module = circuit.build()
    reference = simulate_events(module, [], [event_schedule(case, source)])
    compiled = circuit.compile(optimize=optimize)
    flush = max(8, max(compiled.physical_circuit.output_phases, default=0))

    assert len(reference.reactions) == len(case.occurrences)
    for count, reaction in enumerate(reference.reactions, start=1):
        prefix = replace(case, occurrences=case.occurrences[:count])
        trace = simulate_stream(
            compiled.physical_circuit,
            physical_event_rows(prefix),
            flush_ticks=flush,
        )
        actual = trace[-1][0]
        expected = reaction.state_after["total"]
        if actual != expected:
            raise AssertionError(
                "Event state mismatch: "
                f"occurrence={count - 1}, timestamp={reaction.timestamp}, "
                f"expected={expected}, actual={actual}"
            )

    assert reference.final_state["total"] == reference.reactions[-1].state_after["total"]


@pytest.mark.parametrize("case_seed", _PROGRAM_SEEDS)
@pytest.mark.parametrize("optimize", [False, True])
def test_seeded_direct_event_state_matches_physical_simulation(
    case_seed: int,
    optimize: bool,
) -> None:
    case = generate_event_state_case(case_seed)

    try:
        _assert_event_state_case_equivalent(case, optimize=optimize)
    except AssertionError as original_error:

        def still_fails(candidate: EventStateCase) -> bool:
            try:
                _assert_event_state_case_equivalent(candidate, optimize=optimize)
            except AssertionError:
                return True
            return False

        minimized = shrink_event_state_case(case, still_fails)
        pytest.fail(
            "semantic/physical mismatch for generated direct Event-state case\n"
            f"case_seed={case_seed}\n"
            f"optimize={optimize}\n"
            "original case:\n"
            f"{case.describe()}\n"
            "minimized failing case:\n"
            f"{minimized.describe()}\n"
            f"original mismatch: {original_error}",
            pytrace=False,
        )


def test_event_state_case_shrinker_reduces_schedule_and_transform() -> None:
    case = EventStateCase(
        seed=17,
        scale=-2,
        positive_only=True,
        occurrences=(
            EventCaseOccurrence(0, ((COPPER, 2),)),
            EventCaseOccurrence(8, ((IRON, 3),)),
            EventCaseOccurrence(16, ((COPPER, 4),)),
        ),
    )

    def fails(candidate: EventStateCase) -> bool:
        return any(IRON in occurrence.signal_map() for occurrence in candidate.occurrences)

    minimized = shrink_event_state_case(case, fails)

    assert minimized.scale == 1
    assert minimized.positive_only is False
    assert minimized.occurrences == (EventCaseOccurrence(8, ((IRON, 3),)),)


def test_empty_event_payload_still_sets_valid_lane() -> None:
    case = EventStateCase(
        seed=18,
        scale=1,
        positive_only=False,
        occurrences=(EventCaseOccurrence(2, ()),),
    )

    rows = physical_event_rows(case)

    assert rows[2] == {"source": {}, "source__valid": 1}
