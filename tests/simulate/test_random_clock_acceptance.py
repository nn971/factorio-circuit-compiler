from __future__ import annotations

from factorio_circuit import Circuit
from tests.support.random_clock_acceptance import (
    ClockStructureCase,
    build_heterogeneous_periodic_circuit,
    classify_uniform_periodic_timing,
    shrink_clock_structure,
)
from tests.support.random_crossing_cases import generate_crossing_case
from tests.support.random_event_bridge_cases import generate_event_bridge_case
from tests.support.random_output_materialization_cases import generate_output_materialization_case


def test_nonuniform_periodic_domains_are_explicitly_filtered_from_uniform_oracle() -> None:
    result = build_heterogeneous_periodic_circuit().compile(optimize=False)
    periods = {item.register.name: item.period for item in result.state_timing.registers}

    assert periods == {"fast": 1, "slow": 3}
    assert result.state_timing.uniform_period is None

    support = classify_uniform_periodic_timing(result.state_timing)
    assert not support.supported
    assert support.period is None
    assert support.reason == (
        "non-uniform periodic state domains require per-domain logical-to-physical mapping"
    )


def test_same_index_connection_becomes_supported_uniform_periodic_shape() -> None:
    result = build_heterogeneous_periodic_circuit(connect_outputs=True).compile(optimize=False)
    periods = {item.register.name: item.period for item in result.state_timing.registers}

    assert periods == {"fast": 3, "slow": 3}
    support = classify_uniform_periodic_timing(result.state_timing)
    assert support.supported
    assert support.period == 3
    assert support.reason is None


def test_event_timing_is_routed_to_event_harness_instead_of_periodic_comparator() -> None:
    circuit = Circuit("g8_event_classifier")
    source = circuit.signal_event("source", guaranteed_min_separation=4)
    total = circuit.accumulator("total")
    total.add(source * 1)
    circuit.output("total", total.sample())

    result = circuit.compile(optimize=False)
    support = classify_uniform_periodic_timing(result.state_timing)

    assert not support.supported
    assert support.period is None
    assert support.reason == "Event clocks require the Event differential harness"


def test_clock_structure_reducer_removes_irrelevant_derived_clock_stages() -> None:
    case = ClockStructureCase(
        seed=0xC10C,
        stages=("event_merge", "gate_clock", "sample_on", "valid_materialization"),
    )

    def fails(candidate: ClockStructureCase) -> bool:
        return "gate_clock" in candidate.stages and "sample_on" in candidate.stages

    minimized = shrink_clock_structure(case, fails)

    assert minimized == ClockStructureCase(
        seed=0xC10C,
        stages=("gate_clock", "sample_on"),
    )


def test_representative_random_clock_cases_are_seed_reproducible() -> None:
    seed = 0x5EED

    assert generate_crossing_case(seed) == generate_crossing_case(seed)
    assert generate_event_bridge_case(seed) == generate_event_bridge_case(seed)
    assert generate_output_materialization_case(seed) == generate_output_materialization_case(seed)
