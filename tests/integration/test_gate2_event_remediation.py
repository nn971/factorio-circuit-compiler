from dataclasses import replace

import pytest

from factorio_circuit.analysis.state_timing import analyze_clocked_timing
from factorio_circuit.compiler import compile_circuit
from factorio_circuit.events import (
    EventCausalityError,
    EventCrossingError,
    EventThroughputError,
)
from factorio_circuit.frontend import Circuit, Expr
from factorio_circuit.ir.physical import SignalId
from factorio_circuit.ir.semantic import (
    BinaryOp,
    CircuitModule,
    Clock,
    ClockProvenance,
    Constant,
    EventInput,
    PayloadShape,
    ReturnValue,
    TemporalModality,
    VectorConstant,
    is_vector_value,
)
from factorio_circuit.ir.state import (
    FreezeRegister,
    StateTransition,
    VectorRegisterRead,
    state_transitions,
)
from factorio_circuit.simulate.events import (
    EventMaterializationPolicy,
    EventOccurrence,
    EventSchedule,
    materialize_event_trace,
    simulate_events,
)

IRON = SignalId("item", "iron-plate")


def test_event_flow_operands_execute_with_zero_and_empty_present_payloads() -> None:
    circuit = Circuit("event_flow_execution")
    scalar = circuit.event("scalar", guaranteed_min_separation=1)
    vector = circuit.signal_event("vector", guaranteed_min_separation=1)
    scalar_memory = circuit.freeze("scalar_memory")
    vector_memory = circuit.freeze("vector_memory")
    scalar_value = circuit.constant_signals({IRON: 1}) * (scalar + 1)
    vector_value = vector + circuit.constant_signals({IRON: 2})
    scalar_memory.capture_on(scalar, scalar_value, required_min_separation=1)
    vector_memory.capture_on(vector, vector_value, required_min_separation=1)
    circuit.output("scalar_memory", scalar_memory.value)
    circuit.output("vector_memory", vector_memory.value)
    module = circuit.build()

    result = simulate_events(
        module,
        [{}],
        [
            EventSchedule(scalar, [EventOccurrence(0, 0)]),
            EventSchedule(vector, [EventOccurrence(0, {})]),
        ],
    )

    assert [activation.payload for activation in result.reactions[0].activations] == [0, {}]
    assert result.final_state == {
        "scalar_memory": {IRON: 1},
        "vector_memory": {IRON: 2},
    }


def test_event_flow_shape_modality_and_clock_mixing_are_guarded() -> None:
    circuit = Circuit("event_flow_contracts")
    first = circuit.signal_event("first", guaranteed_min_separation=1)
    second = circuit.signal_event("second", guaranteed_min_separation=1)
    same = first + first
    assert is_vector_value(same.ir)
    assert same.flow is not None
    assert same.flow.payload_shape is PayloadShape.VECTOR
    assert same.flow.modality is TemporalModality.EVENT
    assert same.flow.clock.provenance is ClockProvenance.EXTERNAL_EVENT
    with pytest.raises(EventCausalityError, match="compatible occurrence clock"):
        _ = first + second
    with pytest.raises(EventCrossingError, match="SampleOn"):
        _ = first + circuit.signals("level")


def test_event_mixing_rejects_sampled_level_and_state_reads_but_accepts_sample_on() -> None:
    circuit = Circuit("event_mixing_boundaries")
    scalar = circuit.event("scalar", guaranteed_min_separation=1)
    vector = circuit.signal_event("vector", guaranteed_min_separation=1)
    level = circuit.input("level")
    state = circuit.freeze("state")
    circuit.step()
    with pytest.raises(EventCrossingError):
        _ = scalar + level.sample()
    with pytest.raises(EventCrossingError):
        _ = vector + state.value
    sampled = circuit.sample_on(level, scalar)
    combined = scalar + sampled
    assert combined.flow is not None
    assert combined.flow.modality is TemporalModality.EVENT


def test_output_validation_rejects_an_illegal_expression_even_from_a_malformed_adapter() -> None:
    circuit = Circuit("illegal_output")
    event = circuit.event("event", guaranteed_min_separation=1)
    level = circuit.input("level")
    malformed = object.__new__(BinaryOp)
    object.__setattr__(malformed, "op", "+")
    object.__setattr__(malformed, "left", event._as_expr().ir)
    object.__setattr__(malformed, "right", level.ir)
    object.__setattr__(malformed, "name", None)
    object.__setattr__(malformed, "flow", None)
    with pytest.raises(EventCrossingError):
        circuit.output("illegal", Expr(circuit, malformed))


def test_canonical_and_legacy_state_transition_conflicts_are_rejected() -> None:
    circuit = Circuit("transition_conflict")
    data = circuit.signals("data")
    memory = circuit.freeze("memory")
    memory.set(data, when=1)
    circuit.output("memory", memory.value)
    module = circuit.build()
    canonical = module.transitions[0]
    conflicting = replace(canonical, when=Constant(0), legacy=None)
    with pytest.raises(ValueError, match="conflicting canonical and legacy"):
        state_transitions(replace(module, transitions=(conflicting,)))


def test_sample_on_flow_shapes_transitions_outputs_and_materialization() -> None:
    circuit = Circuit("sample_on_flow")
    scalar_input = circuit.input("scalar")
    vector_input = circuit.signals("vector")
    scalar_event = circuit.event("scalar_event", guaranteed_min_separation=1)
    vector_event = circuit.signal_event("vector_event", guaranteed_min_separation=1)
    scalar_ref = circuit.sample_on(scalar_input + 1, scalar_event)
    vector_ref = circuit.sample_on(vector_input * 2, vector_event)
    memory = circuit.freeze("memory")
    memory.set(vector_ref, when=1)
    circuit.output("scalar", scalar_ref)
    circuit.output("vector", vector_ref + circuit.constant_signals({IRON: 1}))
    circuit.output("memory", memory.value)
    module = circuit.build()

    result = simulate_events(
        module,
        [{"scalar": 2, "vector": {IRON: 3}}, {"scalar": 0, "vector": {}}],
        [
            EventSchedule(scalar_event, [EventOccurrence(0, 0)]),
            EventSchedule(vector_event, [EventOccurrence(0, {})]),
        ],
    )
    scalar_trace = materialize_event_trace(result, scalar_ref, EventMaterializationPolicy.VALID)
    vector_trace = materialize_event_trace(result, vector_ref, EventMaterializationPolicy.HOLD)
    assert scalar_trace.payload_shape is PayloadShape.SCALAR
    assert scalar_trace.payloads == (3, 0)
    assert scalar_trace.valid == (True, False)
    assert vector_trace.payload_shape is PayloadShape.VECTOR
    assert vector_trace.payloads == ({IRON: 6}, {IRON: 6})
    assert result.final_state == {"memory": {IRON: 6}}

    compiled = compile_circuit(circuit)
    assert [port.name for port in compiled.abstract_physical.inputs] == [
        "scalar",
        "vector",
        "scalar_event",
        "scalar_event__valid",
        "vector_event",
        "vector_event__valid",
    ]
    assert [port.name for port in compiled.abstract_physical.outputs] == [
        "scalar",
        "scalar__valid",
        "vector",
        "vector__valid",
        "memory",
    ]


def test_two_sources_at_one_timestamp_commit_atomically_from_one_level_snapshot() -> None:
    circuit = Circuit("atomic_events")
    first = circuit.signal_event("first", guaranteed_min_separation=1)
    second = circuit.signal_event("second", guaranteed_min_separation=1)
    first_memory = circuit.freeze("first_memory")
    second_memory = circuit.freeze("second_memory")
    first_memory.capture_on(first, required_min_separation=1)
    second_memory.capture_on(second, required_min_separation=1)
    circuit.output("first", first_memory.value)
    circuit.output("second", second_memory.value)
    result = simulate_events(
        circuit.build(),
        [{"unused": 1}],
        [
            EventSchedule(first, [EventOccurrence(0, {IRON: 1})]),
            EventSchedule(second, [EventOccurrence(0, {IRON: 2})]),
        ],
    )
    reaction = result.reactions[0]
    assert [activation.source.name for activation in reaction.activations] == ["first", "second"]
    assert reaction.state_before == {"first_memory": {}, "second_memory": {}}
    assert reaction.state_after == {
        "first_memory": {IRON: 1},
        "second_memory": {IRON: 2},
    }


def test_direct_event_state_transitions_keep_independent_irregular_clocks() -> None:
    circuit = Circuit("independent_event_clocks")
    first = circuit.signal_event("first", guaranteed_min_separation=1)
    second = circuit.signal_event("second", guaranteed_min_separation=3)
    first_memory = circuit.freeze("first_memory")
    second_memory = circuit.freeze("second_memory")
    first_memory.set(first + circuit.constant_signals({}), when=1)
    second_memory.set(second + circuit.constant_signals({}), when=1)
    circuit.output("first", first_memory.value)
    circuit.output("second", second_memory.value)

    module = circuit.build()
    plan = analyze_clocked_timing(module)

    assert plan.domains == ()
    assert [item.clock_id.identity for item in plan.event_clocks] == [
        first.ir.clock.identity,
        second.ir.clock.identity,
    ]
    assert [item.required_min_separation for item in plan.event_clocks] == [1, 1]
    result = simulate_events(
        module,
        [{}],
        [
            EventSchedule(first, [EventOccurrence(0, {})]),
            EventSchedule(second, [EventOccurrence(0, {})]),
        ],
    )
    assert result.final_state == {"first_memory": {}, "second_memory": {}}


def test_direct_event_transition_derives_throughput_from_state_latency() -> None:
    circuit = Circuit("derived_event_throughput")
    trigger = circuit.signal_event("finished", guaranteed_min_separation=1)
    memory = circuit.accumulator("memory")
    sampled = circuit.sample_on(memory.sample(), trigger)
    memory.add(sampled + trigger)
    circuit.output("memory", memory.sample())

    plan = analyze_clocked_timing(circuit.build())

    assert plan.event_clocks[0].required_min_separation == 2
    with pytest.raises(EventThroughputError, match="derived minimum separation 2"):
        simulate_events(
            circuit.build(),
            [{}],
            [EventSchedule(trigger, [EventOccurrence(0, {})])],
        )


def test_direct_event_timing_rejects_nonpositive_logical_recurrence_before_throughput() -> None:
    clock = Clock("bad-event", ClockProvenance.EXTERNAL_EVENT)
    source = EventInput("source", PayloadShape.VECTOR, clock)
    register = FreezeRegister("memory")
    module = CircuitModule(
        name="bad_event_recurrence",
        inputs=(),
        operations=(),
        output=ReturnValue((VectorConstant(()),)),
        state_registers=(register,),
        event_inputs=(source,),
        transitions=(
            StateTransition(
                register=register,
                kind="set",
                clock=clock,
                trigger=source,
                value=VectorRegisterRead(register, offset=1),
                when=Constant(1),
            ),
        ),
    )

    with pytest.raises(EventCausalityError, match="nonpositive logical cycle"):
        analyze_clocked_timing(module)


def test_acyclic_zero_displacement_event_edge_is_not_a_causality_error() -> None:
    clock = Clock("zero-edge", ClockProvenance.EXTERNAL_EVENT)
    source = EventInput("source", PayloadShape.VECTOR, clock)
    first = FreezeRegister("first")
    second = FreezeRegister("second")
    module = CircuitModule(
        name="zero_edge_event",
        inputs=(),
        operations=(),
        output=ReturnValue((VectorConstant(()),)),
        state_registers=(first, second),
        event_inputs=(source,),
        transitions=(
            StateTransition(
                register=first,
                kind="capture",
                clock=clock,
                trigger=source,
                value=None,
            ),
            StateTransition(
                register=second,
                kind="set",
                clock=clock,
                trigger=source,
                value=VectorRegisterRead(first, offset=1),
                when=Constant(1),
            ),
        ),
    )

    plan = analyze_clocked_timing(module)

    assert plan.event_clocks[0].required_min_separation == 1


def test_invalid_event_recurrence_precedes_throughput_validation() -> None:
    clock = Clock("invalid-before-throughput", ClockProvenance.EXTERNAL_EVENT)
    source = EventInput("source", PayloadShape.VECTOR, clock)
    register = FreezeRegister("memory")
    module = CircuitModule(
        name="invalid_before_throughput",
        inputs=(),
        operations=(),
        output=ReturnValue((VectorConstant(()),)),
        state_registers=(register,),
        event_inputs=(source,),
        transitions=(
            StateTransition(
                register=register,
                kind="capture",
                clock=clock,
                trigger=source,
                value=VectorRegisterRead(register, offset=1),
                required_min_separation=7,
            ),
        ),
    )

    with pytest.raises(EventCausalityError, match="zero-offset"):
        simulate_events(module, [], [EventSchedule(source, ())])


def test_legacy_capture_derives_latency_and_keeps_public_bound_as_metadata() -> None:
    circuit = Circuit("legacy_capture_latency")
    trigger = circuit.signal_event("finished", guaranteed_min_separation=7)
    data = circuit.signals("data")
    memory = circuit.freeze("memory")
    memory.capture_on(
        trigger,
        (memory.sample() + data).positive(),
        required_min_separation=7,
    )
    circuit.output("memory", memory.sample())

    plan = analyze_clocked_timing(circuit.build())

    timing = plan.event_clocks[0]
    assert timing.required_min_separation == 3
    assert timing.legacy_required_min_separation == 7
    assert plan.uniform_period is None


def test_legacy_bound_does_not_affect_derived_throughput_legality() -> None:
    circuit = Circuit("legacy_bound_diagnostic_only")
    trigger = circuit.signal_event("finished", guaranteed_min_separation=2)
    memory = circuit.freeze("memory")
    memory.capture_on(
        trigger,
        circuit.constant_signals({IRON: 1}),
        required_min_separation=3,
    )
    circuit.output("memory", memory.sample())

    module = circuit.build()
    timing = analyze_clocked_timing(module).event_clocks[0]
    assert timing.required_min_separation == 1
    assert timing.legacy_required_min_separation == 3
    simulate_events(module, [], [EventSchedule(trigger, ())])


def test_unused_event_declaration_does_not_select_event_only_timing() -> None:
    circuit = Circuit("unused_event_timing")
    circuit.event("unused", guaranteed_min_separation=1)
    data = circuit.signals("data")
    memory = circuit.freeze("memory")
    memory.set(data, when=1)
    circuit.step()
    circuit.output("memory", memory.sample())

    plan = analyze_clocked_timing(circuit.build())

    assert plan.event_clocks == ()
    assert plan.domains
    assert plan.uniform_period == 1


def test_event_only_timing_has_no_synthetic_period() -> None:
    circuit = Circuit("event_only_timing")
    event = circuit.event("event", guaranteed_min_separation=1)
    circuit.output("event", event + 1)

    plan = analyze_clocked_timing(circuit.build())

    assert plan.domains == ()
    assert plan.uniform_period is None
    assert plan.registers == ()


def test_mixed_canonical_transitions_keep_semantic_graph_and_physical_crossing_diagnostic() -> None:
    periodic_clock = Clock("periodic", ClockProvenance.FIXED_PERIODIC)
    event_clock = Clock("event", ClockProvenance.EXTERNAL_EVENT)
    event = EventInput("event", PayloadShape.VECTOR, event_clock)
    periodic_register = FreezeRegister("periodic")
    event_register = FreezeRegister("event_register")
    module = CircuitModule(
        name="mixed_transition_timing",
        inputs=(),
        operations=(),
        output=ReturnValue((VectorConstant(()),)),
        state_registers=(periodic_register, event_register),
        event_inputs=(event,),
        transitions=(
            StateTransition(
                register=periodic_register,
                kind="set",
                clock=periodic_clock,
                value=VectorConstant(()),
                when=Constant(1),
            ),
            StateTransition(
                register=event_register,
                kind="set",
                clock=event_clock,
                trigger=event,
                value=VectorRegisterRead(periodic_register),
                when=Constant(1),
            ),
        ),
    )

    plan = analyze_clocked_timing(module)

    assert plan.domains
    assert plan.event_clocks
    assert len(plan.unsupported_crossings) == 1
