from __future__ import annotations

import pytest

from factorio_circuit import Circuit, materialize_output_trace, simulate_events
from factorio_circuit.ir.output import OutputMaterializationPolicy
from factorio_circuit.simulate.physical import simulate_stream
from tests.support.random_event_bridge_cases import (
    EventBridgeCircuit,
    generate_event_bridge_case,
)
from tests.support.random_event_bridge_cases import (
    event_schedules as bridge_event_schedules,
)
from tests.support.random_event_bridge_cases import (
    physical_rows as bridge_physical_rows,
)
from tests.support.random_output_materialization_cases import (
    OutputMaterializationCase,
    ScalarCaseOccurrence,
    build_output_materialization_circuit,
    event_schedule,
    generate_output_materialization_case,
    physical_rows,
    shrink_output_materialization_case,
)

_CASE_SEEDS = (0x0A11CE, 0x0B1A5)
_POLICIES = (
    OutputMaterializationPolicy.ZERO,
    OutputMaterializationPolicy.HOLD,
)


def _assert_output_materialization_equivalent(
    case: OutputMaterializationCase,
    *,
    policy: OutputMaterializationPolicy,
    optimize: bool,
) -> None:
    built = build_output_materialization_circuit(case, policy)
    module = built.circuit.build()
    semantic = simulate_events(
        module,
        (),
        (event_schedule(case, built),),
        stop_timestamp=17,
    )
    expected = materialize_output_trace(semantic, module, "value")
    compiled = built.circuit.compile(optimize=optimize)

    outputs = compiled.physical_circuit.outputs
    assert [port.name for port in outputs] == ["value"]
    phase = outputs[0].phase
    actual = simulate_stream(
        compiled.physical_circuit,
        physical_rows(case),
        flush_ticks=phase,
    )

    assert expected.contract.policy is policy
    assert expected.valid is None
    for timestamp, payload in enumerate(expected.payloads):
        physical_payload = actual[timestamp + phase][0]
        if physical_payload != payload:
            raise AssertionError(
                "output-materialization mismatch: "
                f"policy={policy.value}, timestamp={timestamp}, phase={phase}, "
                f"expected={payload}, actual={physical_payload}"
            )


@pytest.mark.parametrize("case_seed", _CASE_SEEDS)
@pytest.mark.parametrize("policy", _POLICIES)
@pytest.mark.parametrize("optimize", [False, True])
def test_seeded_zero_and_hold_outputs_match_physical_materialization(
    case_seed: int,
    policy: OutputMaterializationPolicy,
    optimize: bool,
) -> None:
    case = generate_output_materialization_case(case_seed)

    try:
        _assert_output_materialization_equivalent(case, policy=policy, optimize=optimize)
    except AssertionError as original_error:

        def still_fails(candidate: OutputMaterializationCase) -> bool:
            try:
                _assert_output_materialization_equivalent(
                    candidate,
                    policy=policy,
                    optimize=optimize,
                )
            except AssertionError:
                return True
            return False

        minimized = shrink_output_materialization_case(case, still_fails)
        pytest.fail(
            "semantic/physical mismatch for generated output materialization\n"
            f"case_seed={case_seed}\n"
            f"policy={policy.value}\n"
            f"optimize={optimize}\n"
            "original case:\n"
            f"{case.describe()}\n"
            "minimized failing case:\n"
            f"{minimized.describe()}\n"
            f"original mismatch: {original_error}",
            pytrace=False,
        )


@pytest.mark.parametrize("optimize", [False, True])
def test_sum_into_default_zero_policy_matches_physical_materialization(optimize: bool) -> None:
    case = generate_event_bridge_case(0xDEFA017)
    circuit = Circuit("random_default_zero_sum_output")
    source = circuit.signal_event("source", guaranteed_min_separation=4)
    target = circuit.event("target", guaranteed_min_separation=4)
    summed = circuit.sum_into(source, target)
    circuit.output("bridge", summed)
    built = EventBridgeCircuit(circuit, source, target)
    module = circuit.build()

    semantic = simulate_events(
        module,
        (),
        bridge_event_schedules(case, built),
        stop_timestamp=17,
    )
    expected = materialize_output_trace(semantic, module, "bridge")
    compiled = circuit.compile(optimize=optimize)

    assert expected.contract.policy is OutputMaterializationPolicy.ZERO
    assert expected.valid is None
    assert [port.name for port in compiled.physical_circuit.outputs] == ["bridge"]
    phase = compiled.physical_circuit.outputs[0].phase
    actual = simulate_stream(
        compiled.physical_circuit,
        bridge_physical_rows(case),
        flush_ticks=phase,
    )

    for timestamp, payload in enumerate(expected.payloads):
        assert actual[timestamp + phase][0] == payload


def test_output_materialization_generator_keeps_zero_payload_occurrence() -> None:
    case = generate_output_materialization_case(28)
    payloads = {occurrence.timestamp: occurrence.payload for occurrence in case.occurrences}

    assert payloads[8] == 0
    assert payloads[4] != 0


def test_output_materialization_shrinker_reduces_trace_and_transform() -> None:
    case = OutputMaterializationCase(
        seed=29,
        scale=-2,
        bias=2,
        occurrences=(
            ScalarCaseOccurrence(4, 3),
            ScalarCaseOccurrence(8, 0),
            ScalarCaseOccurrence(12, 7),
        ),
    )

    def fails(candidate: OutputMaterializationCase) -> bool:
        return any(occurrence.timestamp == 8 for occurrence in candidate.occurrences)

    minimized = shrink_output_materialization_case(case, fails)

    assert minimized.scale == 1
    assert minimized.bias == 0
    assert minimized.occurrences == (ScalarCaseOccurrence(8, 0),)
