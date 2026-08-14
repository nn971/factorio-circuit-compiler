from typing import cast

import pytest

from factorio_circuit import (
    Circuit,
    EventCausalityError,
    EventCompilationError,
    EventCrossingError,
    EventMaterializationError,
    EventMaterializationPolicy,
    EventOccurrence,
    EventSchedule,
    EventScheduleError,
    EventThroughputError,
    ScalarEvent,
    SignalId,
    TimestampDomain,
    VectorEvent,
    compile_circuit,
    materialize_event_trace,
    simulate_events,
)
from factorio_circuit.analysis.state_timing import StateTimingPlan, analyze_state_timing
from factorio_circuit.compiler_legacy import compile_legacy_circuit
from factorio_circuit.frontend import Expr, SignalsExpr
from factorio_circuit.frontend.symbolic import Circuit as SymbolicCircuit
from factorio_circuit.frontend.vector_circuit import Circuit as VectorCircuit
from factorio_circuit.frontend.vector_expr import SignalsExpr as VectorSignalsExpr
from factorio_circuit.ir.semantic import (
    CircuitModule,
    ClockProvenance,
    Constant,
    PayloadShape,
    ReturnValue,
    SampleOn,
    TemporalModality,
    VectorConstant,
)
from factorio_circuit.ir.state import (
    AccumulatorRegister,
    FreezeCapture,
    FreezeRegister,
    FreezeSet,
)
from factorio_circuit.lowering.ir_to_abstract_physical import lower_abstract_physical
from factorio_circuit.lowering.ir_to_physical import lower_naive
from factorio_circuit.lowering.open_vector_pipeline import lower_vectors
from factorio_circuit.optimize.common_subexpr import eliminate_common_subexpressions
from factorio_circuit.optimize.dead_code import eliminate_dead_code
from factorio_circuit.optimize.pipeline import optimize_semantic
from factorio_circuit.optimize.simplify import simplify_module
from factorio_circuit.simulate.semantic import evaluate, simulate_stream

IRON = SignalId("item", "iron-plate")


def _constant_event_module() -> tuple[Circuit, ScalarEvent, VectorEvent]:
    circuit = Circuit("event_decl")
    scalar = circuit.event("finished", guaranteed_min_separation=2)
    vector = circuit.signal_event("contents", guaranteed_min_separation=3)
    circuit.output("constant", 0)
    return circuit, scalar, vector


def test_scalar_and_vector_event_declarations_preserve_order_and_metadata() -> None:
    circuit = Circuit("declarations")
    scalar = circuit.event("finished", guaranteed_min_separation=2)
    vector = circuit.signal_event("contents", guaranteed_min_separation=4)
    later_scalar = circuit.event("arrived", guaranteed_min_separation=1)
    circuit.output("constant", 0)
    module = circuit.build()

    assert [source.name for source in module.event_inputs] == ["finished", "contents", "arrived"]
    assert scalar.name == "finished"
    assert scalar.ir is module.event_inputs[0]
    assert scalar.clock.provenance is ClockProvenance.EXTERNAL_EVENT
    assert scalar.flow.payload_shape is PayloadShape.SCALAR
    assert scalar.flow.modality is TemporalModality.EVENT
    assert scalar.flow.clock == scalar.clock
    assert vector.ir is module.event_inputs[1]
    assert vector.flow.payload_shape is PayloadShape.VECTOR
    assert later_scalar.clock.guaranteed_min_separation == 1
    assert not isinstance(scalar, Expr)
    assert not isinstance(vector, SignalsExpr)
    assert not hasattr(scalar, "sample")
    assert not hasattr(vector, "signal")


def test_event_sources_use_the_existing_name_namespace() -> None:
    circuit = Circuit("event_names")
    circuit.input("taken")
    with pytest.raises(ValueError, match="already used"):
        circuit.event("taken", guaranteed_min_separation=1)


def test_empty_schedule_is_valid_and_schedules_must_cover_sources_exactly_once() -> None:
    circuit, scalar, vector = _constant_event_module()
    module = circuit.build()

    result = simulate_events(
        module,
        [],
        [EventSchedule(scalar, ()), EventSchedule(vector.ir, ())],
    )
    assert result.reactions == ()
    assert result.final_state == {}
    with pytest.raises(EventScheduleError, match="exactly one"):
        simulate_events(module, [], [EventSchedule(scalar, ())])
    with pytest.raises(EventScheduleError, match="duplicate"):
        simulate_events(module, [], [EventSchedule(scalar, ()), EventSchedule(scalar, ())])


def test_schedule_source_must_match_a_declared_source() -> None:
    first, scalar, vector = _constant_event_module()
    other = Circuit("other_event").event("other", guaranteed_min_separation=1)
    first.output("extra", 1)

    with pytest.raises(EventScheduleError, match="not declared"):
        simulate_events(
            first.build(),
            [],
            [EventSchedule(other.ir, ()), EventSchedule(vector.ir, ())],
        )


@pytest.mark.parametrize(
    "occurrences",
    [
        (EventOccurrence(1, 0), EventOccurrence(1, 1)),
        (EventOccurrence(2, 0), EventOccurrence(1, 1)),
    ],
)
def test_event_schedule_timestamps_are_strictly_ordered(
    occurrences: tuple[EventOccurrence, ...],
) -> None:
    circuit = Circuit("timestamp_validation")
    event = circuit.event("finished", guaranteed_min_separation=1)
    circuit.output("constant", 0)
    with pytest.raises(EventScheduleError, match="strictly ordered"):
        simulate_events(circuit.build(), [], [EventSchedule(event.ir, occurrences)])


def test_event_schedule_rejects_bool_negative_and_too_close_timestamps() -> None:
    with pytest.raises(EventScheduleError, match="non-boolean"):
        EventOccurrence(True, 0)
    with pytest.raises(EventScheduleError, match="non-negative"):
        EventOccurrence(-1, 0)

    circuit = Circuit("separation")
    event = circuit.event("finished", guaranteed_min_separation=3)
    circuit.output("constant", 0)
    with pytest.raises(EventScheduleError, match="minimum separation"):
        simulate_events(
            circuit.build(),
            [],
            [
                EventSchedule(
                    event.ir,
                    (EventOccurrence(0, 0), EventOccurrence(2, 0)),
                )
            ],
        )


def test_event_payloads_normalize_i32_and_preserve_zero_empty_presence() -> None:
    circuit = Circuit("payloads")
    scalar = circuit.event("scalar", guaranteed_min_separation=1)
    vector = circuit.signal_event("vector", guaranteed_min_separation=1)
    circuit.output("constant", 0)
    module = circuit.build()
    result = simulate_events(
        module,
        [],
        [
            EventSchedule(scalar.ir, (EventOccurrence(0, 2**31),)),
            EventSchedule(vector.ir, (EventOccurrence(0, {IRON: 0}),)),
        ],
    )

    assert len(result.reactions) == 1
    assert [activation.payload for activation in result.reactions[0].activations] == [-(2**31), {}]
    with pytest.raises(EventScheduleError, match="None"):
        EventOccurrence(0, None)
    with pytest.raises(EventScheduleError, match="integer"):
        simulate_events(
            module,
            [],
            [
                EventSchedule(scalar.ir, (EventOccurrence(0, {}),)),
                EventSchedule(vector.ir, ()),
            ],
        )


def test_scalar_level_snapshot_capture_and_absence_holds_state() -> None:
    circuit = Circuit("scalar_capture")
    data = cast(VectorSignalsExpr, circuit.signals("data"))
    trigger = circuit.event("finished", guaranteed_min_separation=2)
    memory = circuit.freeze("memory")
    memory.capture_on(trigger, data, required_min_separation=2)
    circuit.output("memory", memory.sample())
    module = circuit.build()

    result = simulate_events(
        module,
        [{"data": {IRON: 1}}, {"data": {IRON: 2}}, {"data": {IRON: 3}}],
        [
            EventSchedule(
                trigger.ir,
                (EventOccurrence(0, 0), EventOccurrence(2, 0)),
            )
        ],
    )

    assert result.final_state == {"memory": {IRON: 3}}
    assert len(result.reactions) == 2
    assert result.reactions[0].level_row == {"data": {IRON: 1}}
    assert result.reactions[1].level_row == {"data": {IRON: 3}}


def test_derived_nested_capture_values_reuse_existing_level_expression_semantics() -> None:
    circuit = Circuit("derived_capture")
    data = cast(VectorSignalsExpr, circuit.signals("data"))
    extra = cast(VectorSignalsExpr, circuit.signals("extra"))
    scale = circuit.input("scale")
    trigger = circuit.event("finished", guaranteed_min_separation=1)
    memory = circuit.freeze("memory")
    value = ((data + extra) * ((scale + 1) > 0)).positive().gate(scale != 0)
    memory.capture_on(trigger, value, required_min_separation=1)
    circuit.output("memory", memory.sample())

    result = simulate_events(
        circuit.build(),
        [{"data": {IRON: 2}, "extra": {IRON: 3}, "scale": 1}],
        [EventSchedule(trigger.ir, (EventOccurrence(0, 0),))],
    )

    assert result.final_state == {"memory": {IRON: 5}}


def test_nested_nonzero_offset_capture_leaf_is_rejected() -> None:
    circuit = Circuit("nested_offset_capture")
    data_input = circuit.signals("data")
    data = cast(VectorSignalsExpr, data_input)
    trigger = circuit.signal_event("finished", guaranteed_min_separation=1)
    memory = circuit.freeze("memory")
    circuit.step(1)
    shifted = cast(VectorSignalsExpr, data_input.sample())

    with pytest.raises(EventCausalityError, match="zero-offset"):
        memory.capture_on(trigger, shifted + data, required_min_separation=1)


def test_nested_state_capture_expression_uses_old_state_snapshot() -> None:
    circuit = Circuit("nested_state_capture")
    data = cast(VectorSignalsExpr, circuit.signals("data"))
    trigger = circuit.signal_event("finished", guaranteed_min_separation=1)
    source = circuit.freeze("source")
    target = circuit.freeze("target")
    old_source = source.sample()
    source.capture_on(trigger, required_min_separation=1)
    target.capture_on(trigger, (old_source + data).positive(), required_min_separation=1)
    circuit.output("target", target.sample())

    result = simulate_events(
        circuit.build(),
        [{"data": {IRON: 4}}],
        [EventSchedule(trigger.ir, (EventOccurrence(0, {IRON: 2}),))],
    )

    assert result.final_state == {"source": {IRON: 2}, "target": {IRON: 4}}


def test_event_capture_any_selector_uses_nonempty_old_state_for_second_occurrence() -> None:
    circuit = Circuit("event_any_selector")
    data = cast(VectorSignalsExpr, circuit.signals("data"))
    trigger = circuit.signal_event("finished", guaranteed_min_separation=1)
    source = circuit.freeze("source")
    target = circuit.freeze("target")
    old_source = source.sample()
    source.capture_on(trigger, required_min_separation=1)
    target.capture_on(trigger, data.gate(old_source.any()), required_min_separation=1)
    circuit.output("target", target.sample())

    result = simulate_events(
        circuit.build(),
        [{"data": {IRON: 10}}, {"data": {IRON: 20}}],
        [
            EventSchedule(
                trigger.ir,
                (EventOccurrence(0, {IRON: 1}), EventOccurrence(1, {IRON: 2})),
            )
        ],
    )

    assert result.final_state == {"source": {IRON: 2}, "target": {IRON: 20}}


def test_event_capture_state_operations_are_separate_and_rebuild_safe() -> None:
    circuit = Circuit("event_rebuild")
    data = circuit.signals("data")
    trigger = circuit.signal_event("finished", guaranteed_min_separation=1)
    memory = circuit.freeze("memory")
    memory.capture_on(trigger, data, required_min_separation=1)
    circuit.output("memory", memory.sample())
    module = circuit.build()

    assert module.state_operations == ()
    assert len(module.event_state_operations) == 1
    assert isinstance(module.event_state_operations[0], FreezeCapture)
    for rebuilt in (
        eliminate_dead_code(module),
        simplify_module(module),
        eliminate_common_subexpressions(module),
    ):
        assert rebuilt.state_operations == ()
        assert rebuilt.event_state_operations == module.event_state_operations
        assert rebuilt.event_inputs == module.event_inputs


def test_direct_ir_event_targets_must_be_listed_freeze_registers() -> None:
    circuit = Circuit("direct_targets")
    trigger = circuit.signal_event("finished", guaranteed_min_separation=1)
    value = VectorConstant(((IRON, 1),))
    accumulator = AccumulatorRegister("accumulator")
    unlisted = FreezeRegister("unlisted")
    periodic = FreezeRegister("periodic")

    for operation, registers, periodic_ops in (
        (FreezeCapture(accumulator, trigger.ir, value, 1), (accumulator,), ()),  # type: ignore[arg-type]
        (FreezeCapture(unlisted, trigger.ir, value, 1), (), ()),
        (
            FreezeCapture(periodic, trigger.ir, value, 1),
            (periodic,),
            (FreezeSet(periodic, value, Constant(1), 0),),
        ),
    ):
        module = CircuitModule(
            name="direct_target",
            inputs=(),
            operations=(),
            output=ReturnValue(()),
            state_registers=registers,
            state_operations=periodic_ops,
            event_inputs=(trigger.ir,),
            event_state_operations=(operation,),
        )
        with pytest.raises(EventCausalityError):
            simulate_events(
                module,
                [],
                [EventSchedule(trigger.ir, ())],
            )


def test_outputless_event_modules_are_valid_but_level_modules_still_need_outputs() -> None:
    event_circuit = Circuit("outputless_event")
    event = event_circuit.signal_event("finished", guaranteed_min_separation=1)
    event_module = event_circuit.build()
    assert event_module.output.values == ()
    assert simulate_events(event_module, [], [EventSchedule(event.ir, ())]).final_state == {}

    level_circuit = Circuit("outputless_level")
    level_circuit.input("value")
    with pytest.raises(ValueError, match="no outputs"):
        level_circuit.build()


def test_vector_event_payload_capture_including_empty_payload() -> None:
    circuit = Circuit("vector_capture")
    trigger = circuit.signal_event("finished", guaranteed_min_separation=1)
    memory = circuit.freeze("memory")
    memory.capture_on(trigger, required_min_separation=1)
    circuit.output("memory", memory.sample())

    result = simulate_events(
        circuit.build(),
        [],
        [EventSchedule(trigger.ir, (EventOccurrence(0, {IRON: 4}), EventOccurrence(1, {})))],
    )
    assert result.final_state == {"memory": {}}
    assert [reaction.activations[0].payload for reaction in result.reactions] == [{IRON: 4}, {}]


def test_same_timestamp_reactions_follow_declaration_order_and_commit_atomically() -> None:
    circuit = Circuit("atomic_events")
    first = circuit.signal_event("first", guaranteed_min_separation=1)
    second = circuit.signal_event("second", guaranteed_min_separation=1)
    first_register = circuit.freeze("first_register")
    second_register = circuit.freeze("second_register")
    old_first = first_register.sample()
    first_register.capture_on(first, required_min_separation=1)
    second_register.capture_on(second, old_first, required_min_separation=1)
    circuit.output("first", first_register.sample())
    circuit.output("second", second_register.sample())

    result = simulate_events(
        circuit.build(),
        [],
        [
            EventSchedule(second.ir, (EventOccurrence(0, {IRON: 2}),)),
            EventSchedule(first.ir, (EventOccurrence(0, {IRON: 1}),)),
        ],
    )

    assert len(result.reactions) == 1
    assert [activation.source.name for activation in result.reactions[0].activations] == [
        "first",
        "second",
    ]
    assert result.reactions[0].state_before == {"first_register": {}, "second_register": {}}
    assert result.reactions[0].activations[0].captured_registers == ("first_register",)
    assert result.reactions[0].activations[1].captured_registers == ("second_register",)
    assert result.final_state == {"first_register": {IRON: 1}, "second_register": {}}


@pytest.mark.parametrize("required, expected", [(1, None), (2, None), (3, EventThroughputError)])
def test_capture_throughput_uses_declared_guarantee_not_schedule_luck(
    required: int, expected: type[Exception] | None
) -> None:
    circuit = Circuit(f"throughput_{required}")
    trigger = circuit.signal_event("finished", guaranteed_min_separation=2)
    memory = circuit.freeze("memory")
    memory.capture_on(trigger, required_min_separation=required)
    circuit.output("memory", memory.sample())

    if expected is None:
        simulate_events(circuit.build(), [], [EventSchedule(trigger.ir, ())])
    else:
        with pytest.raises(expected):
            simulate_events(circuit.build(), [], [EventSchedule(trigger.ir, ())])


def test_event_causality_and_compilation_errors_are_distinct() -> None:
    circuit = Circuit("errors")
    trigger = circuit.event("finished", guaranteed_min_separation=1)
    memory = circuit.freeze("memory")
    with pytest.raises(EventCausalityError, match="explicit vector"):
        memory.capture_on(trigger, required_min_separation=1)
    circuit.output("constant", 0)
    module = circuit.build()
    with pytest.raises(EventCompilationError, match="simulate_events"):
        compile_circuit(module)


def test_all_level_only_boundaries_reject_event_modules() -> None:
    circuit = Circuit("boundary_guards")
    trigger = circuit.signal_event("finished", guaranteed_min_separation=1)
    memory = circuit.freeze("memory")
    memory.capture_on(trigger, required_min_separation=1)
    circuit.output("memory", memory.sample())
    module = circuit.build()
    timing = StateTimingPlan((), ())

    routes = (
        lambda: compile_legacy_circuit(module),
        lambda: optimize_semantic(module),
        lambda: analyze_state_timing(module),
        lambda: lower_naive(module, state_timing=timing),
        lambda: lower_vectors(module, enable_packing=False, state_timing=timing),
        lambda: lower_abstract_physical(module, state_timing=timing),
        lambda: evaluate(module, {}),
        lambda: simulate_stream(module, []),
    )
    for route in routes:
        with pytest.raises(EventCompilationError, match="semantic/reference-only"):
            route()


def test_level_simulation_regression_remains_separate_from_event_path() -> None:
    circuit = Circuit("level_regression")
    value = circuit.input("value")
    circuit.output("value", value)

    assert simulate_stream(circuit.build(), [{"value": 7}]) == [(7,)]


def test_sample_on_is_interned_ordered_and_restricted_to_raw_inputs() -> None:
    circuit = Circuit("sample_on")
    scalar_input = circuit.input("scalar")
    vector_input = circuit.signals("vector")
    scalar_event = circuit.event("scalar_event", guaranteed_min_separation=1)
    vector_event = circuit.signal_event("vector_event", guaranteed_min_separation=1)
    scalar_ref = circuit.sample_on(scalar_input, scalar_event)
    assert circuit.sample_on(scalar_input, scalar_event).ir is scalar_ref.ir
    vector_ref = circuit.sample_on(vector_input, vector_event)
    mixed_scalar_ref = circuit.sample_on(scalar_input, vector_event)
    mixed_vector_ref = circuit.sample_on(vector_input, scalar_event)
    assert circuit.sample_on(scalar_input.sample(), scalar_event).ir is scalar_ref.ir  # type: ignore[arg-type]
    assert circuit.sample_on(vector_input.sample(), vector_event).ir is vector_ref.ir  # type: ignore[arg-type]
    circuit.output("constant", 0)
    module = circuit.build()

    assert module.sample_on_crossings == (
        scalar_ref.ir,
        vector_ref.ir,
        mixed_scalar_ref.ir,
        mixed_vector_ref.ir,
    )
    assert not isinstance(scalar_ref, (Expr, SignalsExpr, ScalarEvent, VectorEvent))
    assert not hasattr(scalar_ref, "flow")
    assert scalar_ref.clock == scalar_event.clock
    assert scalar_ref.payload_shape is PayloadShape.SCALAR
    assert mixed_scalar_ref.clock == vector_event.clock
    assert mixed_scalar_ref.payload_shape is PayloadShape.SCALAR
    assert mixed_vector_ref.clock == scalar_event.clock
    assert mixed_vector_ref.payload_shape is PayloadShape.VECTOR

    with pytest.raises(EventCrossingError, match="raw"):
        circuit.sample_on(scalar_input + 1, scalar_event)  # type: ignore[arg-type]
    circuit.step()
    with pytest.raises(EventCrossingError, match="raw"):
        circuit.sample_on(scalar_input.sample(), scalar_event)  # type: ignore[arg-type]
    with pytest.raises(EventCrossingError, match="raw"):
        circuit.sample_on(circuit.constant_signals({IRON: 1}), vector_event)  # type: ignore[arg-type]
    with pytest.raises(EventCrossingError, match="SumInto/HoldInto"):
        circuit.sample_on(scalar_event, vector_event)  # type: ignore[arg-type]
    with pytest.raises(EventCrossingError, match="declared Event"):
        circuit.sample_on(scalar_input, scalar_event.clock)  # type: ignore[arg-type]


def test_sample_on_observations_and_reference_materialization_are_semantic_only() -> None:
    circuit = Circuit("sample_on_trace")
    value = circuit.input("value")
    trigger = circuit.event("trigger", guaranteed_min_separation=1)
    crossing = circuit.sample_on(value, trigger)
    circuit.output("constant", 0)
    result = simulate_events(
        circuit.build(),
        [{"value": 4}, {"value": 0}, {"value": 7}],
        [
            EventSchedule(
                trigger,
                (EventOccurrence(0, 0), EventOccurrence(2, 0)),
            )
        ],
    )

    assert result.domain.start == 0
    assert result.domain.stop == 3
    assert [
        activation.crossing_observations[0].value
        for reaction in result.reactions
        for activation in reaction.activations
    ] == [4, 7]
    hold = materialize_event_trace(result, crossing, EventMaterializationPolicy.HOLD)
    zero = materialize_event_trace(result, crossing, EventMaterializationPolicy.ZERO)
    valid = materialize_event_trace(result, crossing, EventMaterializationPolicy.VALID)
    assert hold.payloads == (4, 4, 7)
    assert zero.payloads == (4, 0, 7)
    assert valid.payloads == (4, 0, 7)
    assert hold.payload_shape is PayloadShape.SCALAR
    assert hold.domain == result.domain
    assert valid.valid == (True, False, True)
    with pytest.raises(EventMaterializationError, match="outputs"):
        circuit.output("trace", valid)  # type: ignore[arg-type]

    with pytest.raises(EventMaterializationError, match="EventMaterializationPolicy"):
        materialize_event_trace(result, crossing, "hold")  # type: ignore[arg-type]
    with pytest.raises(EventScheduleError, match="half-open"):
        simulate_events(
            circuit.build(),
            [],
            [EventSchedule(trigger, (EventOccurrence(3, 0),))],
            stop_timestamp=3,
        )


def test_sample_on_vector_rows_are_copied_and_zero_empty_presence_is_valid() -> None:
    circuit = Circuit("sample_on_vector_trace")
    value = circuit.signals("value")
    trigger = circuit.signal_event("trigger", guaranteed_min_separation=1)
    crossing = circuit.sample_on(value, trigger)
    circuit.output("constant", 0)
    result = simulate_events(
        circuit.build(),
        [{"value": {IRON: 2}}, {"value": {}}],
        [EventSchedule(trigger, (EventOccurrence(0, {}),))],
        stop_timestamp=2,
    )
    trace = materialize_event_trace(result, crossing, EventMaterializationPolicy.VALID)
    assert trace.payloads == ({IRON: 2}, {})
    assert trace.valid == (True, False)
    assert trace.payloads[0] is not trace.payloads[1]


def test_mixed_shape_crossings_and_event_materialization_use_source_shapes() -> None:
    circuit = Circuit("mixed_sample_on")
    scalar = circuit.input("scalar")
    vector = circuit.signals("vector")
    scalar_event = circuit.event("scalar_event", guaranteed_min_separation=1)
    vector_event = circuit.signal_event("vector_event", guaranteed_min_separation=1)
    scalar_crossing = circuit.sample_on(scalar, vector_event)
    vector_crossing = circuit.sample_on(vector, scalar_event)
    circuit.output("constant", 0)
    result = simulate_events(
        circuit.build(),
        [{"scalar": 3, "vector": {IRON: 9}}, {"scalar": 4, "vector": {}}],
        [
            EventSchedule(scalar_event, (EventOccurrence(1, 0),)),
            EventSchedule(vector_event, (EventOccurrence(0, {IRON: 1}),)),
        ],
        stop_timestamp=2,
    )

    assert result.reactions[0].activations[0].crossing_observations[0].value == 3
    assert result.reactions[1].activations[0].crossing_observations[0].value == {}
    scalar_trace = materialize_event_trace(
        result, scalar_crossing, EventMaterializationPolicy.VALID
    )
    vector_trace = materialize_event_trace(
        result, vector_crossing, EventMaterializationPolicy.VALID
    )
    event_scalar_trace = materialize_event_trace(
        result, scalar_event, EventMaterializationPolicy.VALID
    )
    event_vector_trace = materialize_event_trace(
        result, vector_event, EventMaterializationPolicy.VALID
    )
    assert scalar_trace.payload_shape is PayloadShape.SCALAR
    assert scalar_trace.payloads == (3, 0)
    assert scalar_trace.valid == (True, False)
    assert vector_trace.payload_shape is PayloadShape.VECTOR
    assert vector_trace.payloads == ({}, {})
    assert vector_trace.valid == (False, True)
    assert event_scalar_trace.payload_shape is PayloadShape.SCALAR
    assert event_scalar_trace.payloads == (0, 0)
    assert event_scalar_trace.valid == (False, True)
    assert event_vector_trace.payload_shape is PayloadShape.VECTOR
    assert event_vector_trace.payloads == ({IRON: 1}, {})
    assert event_vector_trace.valid == (True, False)


def test_event_hold_startup_tail_and_empty_schedule_domain() -> None:
    circuit = Circuit("event_materialization")
    event = circuit.event("event", guaranteed_min_separation=1)
    circuit.output("constant", 0)
    result = simulate_events(
        circuit.build(),
        [],
        [EventSchedule(event, (EventOccurrence(0, 0), EventOccurrence(2, 5)))],
        stop_timestamp=5,
    )
    hold = materialize_event_trace(result, event, EventMaterializationPolicy.HOLD)
    zero = materialize_event_trace(result, event, EventMaterializationPolicy.ZERO)
    valid = materialize_event_trace(result, event, EventMaterializationPolicy.VALID)
    assert hold.payloads == (0, 0, 5, 5, 5)
    assert zero.payloads == (0, 0, 5, 0, 0)
    assert valid.payloads == (0, 0, 5, 0, 0)
    assert valid.valid == (True, False, True, False, False)

    vector_circuit = Circuit("empty_event_materialization")
    vector_event = vector_circuit.signal_event("event", guaranteed_min_separation=1)
    vector_circuit.output("constant", 0)
    vector_result = simulate_events(
        vector_circuit.build(),
        [],
        [EventSchedule(vector_event, (EventOccurrence(0, {}),))],
        stop_timestamp=2,
    )
    vector_valid = materialize_event_trace(
        vector_result, vector_event, EventMaterializationPolicy.VALID
    )
    assert vector_valid.payloads == ({}, {})
    assert vector_valid.valid == (True, False)

    empty = simulate_events(circuit.build(), [{"unused": 1}], [EventSchedule(event, ())])
    assert empty.reactions == ()
    assert empty.domain == TimestampDomain(start=0, stop=1)


def test_sample_on_direct_ir_validation_and_sample_only_boundary() -> None:
    circuit = Circuit("direct_sample_on")
    source = circuit.input("source")
    event = circuit.event("event", guaranteed_min_separation=1)
    crossing = circuit.sample_on(source, event)
    module = circuit.build()
    duplicate = CircuitModule(
        name="duplicate_sample_on",
        inputs=module.inputs,
        operations=(),
        output=ReturnValue(()),
        event_inputs=module.event_inputs,
        sample_on_crossings=(crossing.ir, crossing.ir),
    )
    with pytest.raises(EventCrossingError, match="duplicate"):
        simulate_events(duplicate, [], [EventSchedule(event, ())])

    undeclared = CircuitModule(
        name="undeclared_sample_on",
        inputs=(),
        operations=(),
        output=ReturnValue(()),
        event_inputs=module.event_inputs,
        sample_on_crossings=(SampleOn(module.inputs[0], module.event_inputs[0]),),
    )
    with pytest.raises(EventCrossingError, match="not a declared"):
        simulate_events(undeclared, [], [EventSchedule(event, ())])
    for rebuilt in (
        eliminate_dead_code(module),
        simplify_module(module),
        eliminate_common_subexpressions(module),
    ):
        assert rebuilt.sample_on_crossings == module.sample_on_crossings
    with pytest.raises(EventCompilationError, match="semantic/reference-only"):
        compile_circuit(module)
    timing = StateTimingPlan((), ())
    for route in (
        lambda: compile_legacy_circuit(module),
        lambda: optimize_semantic(module),
        lambda: analyze_state_timing(module),
        lambda: lower_naive(module, state_timing=timing),
        lambda: lower_vectors(module, enable_packing=False, state_timing=timing),
        lambda: lower_abstract_physical(module, state_timing=timing),
        lambda: evaluate(module, {}),
        lambda: simulate_stream(module, []),
    ):
        with pytest.raises(EventCompilationError, match="semantic/reference-only"):
            route()

    sample_only = simulate_events(
        module,
        [{"source": 8}],
        [EventSchedule(event, ())],
    )
    assert sample_only.domain == TimestampDomain(start=0, stop=1)
    assert materialize_event_trace(
        sample_only, crossing, EventMaterializationPolicy.HOLD
    ).payloads == (0,)


def test_zero_offset_sample_fast_paths_remain_identity_in_both_frontends() -> None:
    symbolic = SymbolicCircuit("symbolic_zero_offset")
    scalar = symbolic.input("scalar")
    vector = symbolic.signals("vector")
    assert scalar.sample() is scalar
    assert vector.sample() is vector

    vector_frontend = VectorCircuit("vector_zero_offset")
    scalar = vector_frontend.input("scalar")
    vector = vector_frontend.signals("vector")
    assert scalar.sample() is scalar
    assert vector.sample() is vector


def test_sample_on_cannot_be_captured_or_emitted() -> None:
    circuit = Circuit("sample_on_boundaries")
    value = circuit.input("value")
    trigger = circuit.event("trigger", guaranteed_min_separation=1)
    crossing = circuit.sample_on(value, trigger)
    memory = circuit.freeze("memory")
    with pytest.raises(EventCrossingError, match="capture trigger"):
        memory.capture_on(crossing, required_min_separation=1)  # type: ignore[arg-type]
    with pytest.raises(EventCrossingError, match="outputs"):
        circuit.output("crossing", crossing)  # type: ignore[arg-type]
