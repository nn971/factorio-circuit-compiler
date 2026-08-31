from __future__ import annotations

import pytest

from factorio_circuit import SignalId, materialize_output_trace, simulate_events
from factorio_circuit.simulate.physical import simulate_stream
from tests.support.random_event_bridge_cases import (
    EventBridgeCase,
    ScalarCaseOccurrence,
    VectorCaseOccurrence,
    build_event_bridge_circuit,
    event_schedules,
    generate_event_bridge_case,
    physical_rows,
    shrink_event_bridge_case,
)

_CASE_SEEDS = (0x5A17, 0xB01D)
_BRIDGE_KINDS = ("sum", "hold")


def _assert_event_bridge_equivalent(
    case: EventBridgeCase,
    *,
    kind: str,
    optimize: bool,
) -> None:
    built = build_event_bridge_circuit(case, kind)
    module = built.circuit.build()
    semantic = simulate_events(
        module,
        (),
        event_schedules(case, built),
        stop_timestamp=17,
    )
    expected = materialize_output_trace(semantic, module, "bridge")
    compiled = built.circuit.compile(optimize=optimize)

    outputs = compiled.physical_circuit.outputs
    assert [port.name for port in outputs] == ["bridge", "bridge__valid"]
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
                "event-bridge/materialization mismatch: "
                f"kind={kind}, timestamp={timestamp}, phase={phase}, "
                f"expected={(payload, int(valid))}, "
                f"actual={(physical_payload, physical_valid)}"
            )


@pytest.mark.parametrize("case_seed", _CASE_SEEDS)
@pytest.mark.parametrize("kind", _BRIDGE_KINDS)
@pytest.mark.parametrize("optimize", [False, True])
def test_seeded_sum_and_hold_bridges_match_physical_materialization(
    case_seed: int,
    kind: str,
    optimize: bool,
) -> None:
    case = generate_event_bridge_case(case_seed)

    try:
        _assert_event_bridge_equivalent(case, kind=kind, optimize=optimize)
    except AssertionError as original_error:

        def still_fails(candidate: EventBridgeCase) -> bool:
            try:
                _assert_event_bridge_equivalent(candidate, kind=kind, optimize=optimize)
            except AssertionError:
                return True
            return False

        minimized = shrink_event_bridge_case(case, still_fails)
        pytest.fail(
            "semantic/physical mismatch for generated event bridge\n"
            f"case_seed={case_seed}\n"
            f"kind={kind}\n"
            f"optimize={optimize}\n"
            "original case:\n"
            f"{case.describe()}\n"
            "minimized failing case:\n"
            f"{minimized.describe()}\n"
            f"original mismatch: {original_error}",
            pytrace=False,
        )


def test_event_bridge_generator_guarantees_boundary_cases() -> None:
    case = generate_event_bridge_case(26)
    source_timestamps = {occurrence.timestamp for occurrence in case.source}
    target_timestamps = {occurrence.timestamp for occurrence in case.target}

    assert 0 in target_timestamps
    assert 4 in source_timestamps
    assert 8 in source_timestamps and 8 in target_timestamps
    assert 16 in target_timestamps


def test_event_bridge_shrinker_reduces_schedule_and_payload_lanes() -> None:
    iron = SignalId("item", "iron-plate")
    copper = SignalId("item", "copper-plate")
    case = EventBridgeCase(
        seed=27,
        source=(
            VectorCaseOccurrence(4, ((copper, 3),)),
            VectorCaseOccurrence(8, ((copper, 5), (iron, 7))),
        ),
        target=(
            ScalarCaseOccurrence(0, 1),
            ScalarCaseOccurrence(8, 1),
        ),
    )

    def fails(candidate: EventBridgeCase) -> bool:
        return any(
            occurrence.timestamp == 8 and iron in dict(occurrence.payload)
            for occurrence in candidate.source
        )

    minimized = shrink_event_bridge_case(case, fails)

    assert minimized.target == ()
    assert minimized.source == (VectorCaseOccurrence(8, ((iron, 7),)),)
