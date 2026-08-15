from typing import cast

import pytest

from factorio_circuit import (
    Circuit,
    CircuitBuildError,
    EventOccurrence,
    EventSchedule,
    MaterializedReturnValue,
    OutputMaterializationPolicy,
    VectorEvent,
    compile_circuit,
    materialize_output_trace,
    output_materializations,
    simulate_events,
)
from factorio_circuit.frontend import Expr


def test_level_output_defaults_to_hold_and_survives_compiler_normalization() -> None:
    circuit = Circuit("level_output_policy")
    value = circuit.input("value")
    circuit.output("value", value)

    module = circuit.build()
    assert isinstance(module.output, MaterializedReturnValue)
    assert module.output.contract_for(0).policy is OutputMaterializationPolicy.HOLD
    assert module.output.contract_for(0).valid_name is None

    compiled = compile_circuit(circuit)
    assert output_materializations(compiled.semantic_ir.output)[0].policy is (
        OutputMaterializationPolicy.HOLD
    )
    assert output_materializations(compiled.optimized_ir.output)[0].policy is (
        OutputMaterializationPolicy.HOLD
    )


def test_event_output_defaults_distinguish_additive_and_general_streams() -> None:
    circuit = Circuit("event_output_defaults")
    general = circuit.event("general", guaranteed_min_separation=1)
    left = circuit.signal_event("left", guaranteed_min_separation=1)
    right = circuit.signal_event("right", guaranteed_min_separation=1)
    target = circuit.event("target", guaranteed_min_separation=2)
    merged = cast(VectorEvent, circuit.event_merge(left, right))
    summed = circuit.sum_into(merged, target)

    circuit.output("general_out", general)
    circuit.output("merged_out", merged)
    circuit.output("summed_out", summed)
    output = cast(MaterializedReturnValue, circuit.build().output)

    assert [item.policy for item in output.materializations] == [
        OutputMaterializationPolicy.VALID,
        OutputMaterializationPolicy.ZERO,
        OutputMaterializationPolicy.ZERO,
    ]
    assert output.contract_for(0).valid_name == "general_out__valid"
    assert output.contract_for(1).valid_name is None
    assert output.contract_for(2).valid_name is None


def test_explicit_policy_override_and_valid_name_validation() -> None:
    circuit = Circuit("output_override")
    first = circuit.event("first", guaranteed_min_separation=1)
    second = circuit.event("second", guaranteed_min_separation=1)

    circuit.output("pulse", first, policy=OutputMaterializationPolicy.ZERO)
    circuit.output(
        "sample",
        second,
        policy=OutputMaterializationPolicy.VALID,
        valid_name="sample_present",
    )
    output = cast(MaterializedReturnValue, circuit.build().output)
    assert output.contract_for(0).policy is OutputMaterializationPolicy.ZERO
    assert output.contract_for(1).valid_name == "sample_present"

    bad_policy = Circuit("bad_output_policy")
    event = bad_policy.event("event", guaranteed_min_separation=1)
    with pytest.raises(CircuitBuildError, match="OutputMaterializationPolicy"):
        bad_policy.output("event", event, policy="zero")  # type: ignore[arg-type]

    bad_valid = Circuit("bad_valid_name")
    event = bad_valid.event("event", guaranteed_min_separation=1)
    with pytest.raises(ValueError, match="only VALID"):
        bad_valid.output(
            "event",
            event,
            policy=OutputMaterializationPolicy.ZERO,
            valid_name="unexpected_valid",
        )


def test_valid_companion_names_cannot_collide_with_payload_outputs() -> None:
    circuit = Circuit("valid_name_collision")
    first = circuit.event("first", guaranteed_min_separation=1)
    second = circuit.event("second", guaranteed_min_separation=1)
    circuit.output("first_out", first, valid_name="second_out")
    circuit.output("second_out", second)

    with pytest.raises(ValueError, match="must be unique"):
        circuit.build()


def test_irregular_general_event_output_aligns_payload_and_valid() -> None:
    circuit = Circuit("irregular_valid_output")
    event = circuit.event("event", guaranteed_min_separation=1)
    expression = cast(Expr, event * 2 + 1)
    circuit.output("value", expression)
    module = circuit.build()

    result = simulate_events(
        module,
        (),
        (
            EventSchedule(
                event,
                (
                    EventOccurrence(1, 3),
                    EventOccurrence(4, 0),
                ),
            ),
        ),
        stop_timestamp=6,
    )
    trace = materialize_output_trace(result, module, "value")

    assert trace.contract.policy is OutputMaterializationPolicy.VALID
    assert trace.valid_name == "value__valid"
    assert trace.payloads == (0, 7, 0, 0, 1, 0)
    assert trace.valid == (False, True, False, False, True, False)


def test_additive_event_merge_output_uses_zero_between_irregular_occurrences() -> None:
    circuit = Circuit("irregular_zero_output")
    left = circuit.event("left", guaranteed_min_separation=1)
    right = circuit.event("right", guaranteed_min_separation=1)
    merged = circuit.event_merge(left, right)
    circuit.output("total", merged)
    module = circuit.build()

    result = simulate_events(
        module,
        (),
        (
            EventSchedule(left, (EventOccurrence(1, 2), EventOccurrence(5, 4))),
            EventSchedule(right, (EventOccurrence(3, 7),)),
        ),
        stop_timestamp=7,
    )
    trace = materialize_output_trace(result, module, 0)

    assert trace.contract.policy is OutputMaterializationPolicy.ZERO
    assert trace.payloads == (0, 2, 0, 7, 0, 4, 0)
    assert trace.valid is None


def test_explicit_hold_event_output_retains_last_payload() -> None:
    circuit = Circuit("event_hold_output")
    event = circuit.event("event", guaranteed_min_separation=1)
    circuit.output("held", event, policy=OutputMaterializationPolicy.HOLD)
    module = circuit.build()

    result = simulate_events(
        module,
        (),
        (EventSchedule(event, (EventOccurrence(2, 5), EventOccurrence(5, 9))),),
        stop_timestamp=7,
    )
    trace = materialize_output_trace(result, module, "held")

    assert trace.payloads == (0, 0, 5, 5, 5, 9, 9)
    assert trace.valid is None
