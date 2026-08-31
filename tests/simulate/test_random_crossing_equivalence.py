from __future__ import annotations

from dataclasses import replace

import pytest

from factorio_circuit import materialize_output_trace, simulate_events
from factorio_circuit.simulate.physical import simulate_stream
from tests.support.random_crossing_cases import (
    CrossingCase,
    ScalarCaseOccurrence,
    build_crossing_circuit,
    event_schedules,
    generate_crossing_case,
    level_stream,
    physical_rows,
    shrink_crossing_case,
)

_PROGRAM_SEEDS = (0xC1055, 0x6A7E, 0x5A6E)


def _assert_crossing_case_equivalent(case: CrossingCase, *, optimize: bool) -> None:
    built = build_crossing_circuit(case)
    module = built.circuit.build()
    semantic = simulate_events(
        module,
        level_stream(case),
        event_schedules(case, built),
        stop_timestamp=len(case.data),
    )
    expected = materialize_output_trace(semantic, module, "sampled")
    compiled = built.circuit.compile(optimize=optimize)

    outputs = compiled.physical_circuit.outputs
    assert [port.name for port in outputs] == ["sampled", "sampled__valid"]
    assert outputs[0].phase == outputs[1].phase
    phase = outputs[0].phase
    actual = simulate_stream(
        compiled.physical_circuit,
        physical_rows(case),
        flush_ticks=phase,
    )

    assert expected.valid is not None
    expected_rows = zip(expected.payloads, expected.valid, strict=True)
    for timestamp, (payload, valid) in enumerate(expected_rows):
        physical_payload, physical_valid = actual[timestamp + phase]
        if (physical_payload, physical_valid) != (payload, int(valid)):
            raise AssertionError(
                "crossing/materialization mismatch: "
                f"timestamp={timestamp}, phase={phase}, "
                f"expected={(payload, int(valid))}, "
                f"actual={(physical_payload, physical_valid)}"
            )


@pytest.mark.parametrize("case_seed", _PROGRAM_SEEDS)
@pytest.mark.parametrize("optimize", [False, True])
def test_seeded_merge_gate_sample_crossings_match_physical_materialization(
    case_seed: int,
    optimize: bool,
) -> None:
    case = generate_crossing_case(case_seed)

    try:
        _assert_crossing_case_equivalent(case, optimize=optimize)
    except AssertionError as original_error:

        def still_fails(candidate: CrossingCase) -> bool:
            try:
                _assert_crossing_case_equivalent(candidate, optimize=optimize)
            except AssertionError:
                return True
            return False

        minimized = shrink_crossing_case(case, still_fails)
        pytest.fail(
            "semantic/physical mismatch for generated crossing case\n"
            f"case_seed={case_seed}\n"
            f"optimize={optimize}\n"
            "original case:\n"
            f"{case.describe()}\n"
            "minimized failing case:\n"
            f"{minimized.describe()}\n"
            f"original mismatch: {original_error}",
            pytrace=False,
        )


def test_crossing_generator_preserves_cancelled_merged_occurrence() -> None:
    case = generate_crossing_case(23)
    left = {item.timestamp: item.payload for item in case.left}
    right = {item.timestamp: item.payload for item in case.right}

    assert 8 in left and 8 in right
    assert left[8] + right[8] == 0
    assert case.enabled[8] == 1


def test_crossing_case_shrinker_reduces_schedules_and_level_rows() -> None:
    case = CrossingCase(
        seed=24,
        scale=-2,
        bias=2,
        enabled=(1,) + (0,) * 16,
        data=(7,) + (0,) * 16,
        left=(
            ScalarCaseOccurrence(0, 2),
            ScalarCaseOccurrence(4, 3),
        ),
        right=(ScalarCaseOccurrence(8, 5),),
    )

    def fails(candidate: CrossingCase) -> bool:
        return any(item.timestamp == 4 for item in candidate.left)

    minimized = shrink_crossing_case(case, fails)

    assert minimized.scale == 1
    assert minimized.bias == 0
    assert not any(minimized.enabled)
    assert not any(minimized.data)
    assert minimized.left == (ScalarCaseOccurrence(4, 3),)
    assert minimized.right == ()


def test_crossing_case_reducer_can_remove_cancel_pair_independently() -> None:
    case = generate_crossing_case(25)
    candidate = replace(
        case,
        left=tuple(item for item in case.left if item.timestamp != 8),
    )

    assert all(item.timestamp != 8 for item in candidate.left)
    assert any(item.timestamp == 8 for item in candidate.right)
