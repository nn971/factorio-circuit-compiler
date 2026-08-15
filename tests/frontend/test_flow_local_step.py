import pytest

from factorio_circuit import (
    Circuit,
    CircuitBuildError,
    EventCausalityError,
    EventOccurrence,
    EventSchedule,
    Expr,
    SignalId,
    SignalsExpr,
    compile_circuit,
    simulate_events,
)
from factorio_circuit.ir.semantic import (
    BinaryOp,
    FlowInputSample,
    FlowVectorInputSample,
    InputSample,
    VectorBinaryOp,
    VectorInputSample,
)
from factorio_circuit.ir.state import VectorRegisterRead
from factorio_circuit.lowering.frontend_to_ir import normalize_module
from factorio_circuit.simulate.compare import assert_same_stream

IRON = SignalId("item", "iron-plate")


def test_scalar_step_is_local_compositional_and_flow_backed() -> None:
    circuit = Circuit("scalar_local_step")
    source = circuit.input("source")
    current = source + 1
    later = current.step().step(2)

    assert isinstance(source, Expr)
    assert circuit.now.offset == 0
    assert later.step(0) is later

    circuit.output("current", current)
    circuit.output("later", later)
    raw = circuit.build()
    later_raw = raw.output.values[1]
    assert isinstance(later_raw, BinaryOp)
    assert isinstance(later_raw.left, InputSample)
    assert later_raw.left.offset == 3

    normalized = normalize_module(raw)
    current_flow = normalized.output.values[0].flow  # type: ignore[attr-defined]
    later_flow = normalized.output.values[1].flow  # type: ignore[attr-defined]
    assert current_flow.logical_offset == 0
    assert later_flow.logical_offset == 3
    assert current_flow.clock == later_flow.clock
    assert isinstance(normalized.output.values[1].left, FlowInputSample)  # type: ignore[attr-defined]


def test_vector_step_is_local_compositional_and_keeps_vectors_packed() -> None:
    circuit = Circuit("vector_local_step")
    source = circuit.signals("source")
    current = source + circuit.constant_signals({IRON: 1})
    later = current.step(2).step()

    assert circuit.now.offset == 0
    assert later.step(0) is later

    circuit.output("current", current)
    circuit.output("later", later)
    raw = circuit.build()
    later_raw = raw.output.values[1]
    assert isinstance(later_raw, VectorBinaryOp)
    assert isinstance(later_raw.left, VectorInputSample)
    assert later_raw.left.offset == 3

    normalized = normalize_module(raw)
    later_normalized = normalized.output.values[1]
    assert isinstance(later_normalized, VectorBinaryOp)
    assert later_normalized.flow is not None
    assert later_normalized.flow.logical_offset == 3
    assert isinstance(later_normalized.left, FlowVectorInputSample)


def test_flow_local_step_does_not_introduce_state() -> None:
    circuit = Circuit("stateless_local_step")
    source = circuit.input("source")
    circuit.output("later", (source + 1).step(2))

    result = compile_circuit(circuit, optimize=False)

    assert result.semantic_ir.state_registers == ()
    assert result.state_timing.registers == ()


def test_register_step_reindexes_the_read_without_advancing_circuit_cursor() -> None:
    circuit = Circuit("register_local_step")
    memory = circuit.freeze("memory")

    current = memory.sample()
    later = current.step(2)

    assert circuit.now.offset == 0
    assert isinstance(current.ir, VectorRegisterRead)
    assert isinstance(later.ir, VectorRegisterRead)
    assert current.ir.offset == 0
    assert later.ir.offset == 2
    assert current.ir.register == later.ir.register
    assert current.ir.order == later.ir.order


def _compile_state_read(*, local_step: bool):
    circuit = Circuit("state_local" if local_step else "state_cursor")
    data = circuit.signals("data")
    memory = circuit.freeze("memory")
    memory.set(data, when=1)
    if local_step:
        new_value = memory.sample().step()
        assert circuit.now.offset == 0
    else:
        circuit.step()
        new_value = memory.sample()
    circuit.output("memory", new_value)
    return compile_circuit(circuit, optimize=False)


def test_flow_local_step_matches_legacy_cursor_for_next_state_observation() -> None:
    local = _compile_state_read(local_step=True)
    legacy = _compile_state_read(local_step=False)

    local_timing = local.state_timing.registers[0]
    legacy_timing = legacy.state_timing.registers[0]
    assert local_timing.period == legacy_timing.period
    assert local_timing.commit_offset == legacy_timing.commit_offset
    assert local_timing.state_phase == legacy_timing.state_phase
    assert local.physical_circuit.output_phases == legacy.physical_circuit.output_phases

    stream = [
        {"data": {IRON: 1}},
        {"data": {IRON: 2}},
        {"data": {IRON: 3}},
    ]
    assert_same_stream(local.semantic_ir, local.physical_circuit, stream)
    assert_same_stream(legacy.semantic_ir, legacy.physical_circuit, stream)


@pytest.mark.parametrize("bad", [-1, True, 1.5])
def test_flow_local_step_rejects_invalid_displacements(bad: object) -> None:
    circuit = Circuit("bad_local_step")
    source = circuit.input("source")
    with pytest.raises(CircuitBuildError, match="non-negative integer"):
        source.step(bad)  # type: ignore[arg-type]


def test_event_source_and_derived_step_carry_occurrence_offsets_locally() -> None:
    circuit = Circuit("event_local_step")
    event = circuit.event("event", guaranteed_min_separation=1)

    source_later = event.step(2)
    derived_later = (event + 1).step(3)

    assert circuit.now.offset == 0
    assert source_later.flow is not None
    assert source_later.flow.logical_offset == 2
    assert source_later.flow.clock == event.clock
    assert derived_later.flow is not None
    assert derived_later.flow.logical_offset == 3
    assert derived_later.flow.clock == event.clock


def test_event_transition_step_skips_the_reindexed_prefix() -> None:
    circuit = Circuit("event_transition_step")
    event = circuit.signal_event("event", guaranteed_min_separation=1)
    accumulator = circuit.accumulator("accumulator")
    accumulator.add(event.step(1))
    circuit.output("accumulator", accumulator.sample())
    module = circuit.build()

    assert len(module.transitions) == 1
    assert module.transitions[0].logical_offset == 1

    result = simulate_events(
        module,
        [],
        [
            EventSchedule(
                event,
                (
                    EventOccurrence(0, {IRON: 1}),
                    EventOccurrence(1, {IRON: 2}),
                    EventOccurrence(2, {IRON: 3}),
                ),
            )
        ],
    )

    assert [reaction.state_after["accumulator"] for reaction in result.reactions] == [
        {},
        {IRON: 2},
        {IRON: 5},
    ]
    assert result.final_state == {"accumulator": {IRON: 5}}


def test_sample_on_step_uses_the_reindexed_target_occurrence() -> None:
    circuit = Circuit("sample_on_transition_step")
    data = circuit.signals("data")
    event = circuit.event("event", guaranteed_min_separation=1)
    sampled = circuit.sample_on(data, event).step(1)
    assert isinstance(sampled, SignalsExpr)
    accumulator = circuit.accumulator("accumulator")
    accumulator.add(sampled)
    circuit.output("accumulator", accumulator.sample())
    module = circuit.build()

    assert module.transitions[0].logical_offset == 1
    result = simulate_events(
        module,
        [
            {"data": {IRON: 10}},
            {"data": {IRON: 20}},
            {"data": {IRON: 30}},
        ],
        [
            EventSchedule(
                event,
                (
                    EventOccurrence(0, 0),
                    EventOccurrence(1, 0),
                    EventOccurrence(2, 0),
                ),
            )
        ],
    )

    assert [reaction.state_after["accumulator"] for reaction in result.reactions] == [
        {},
        {IRON: 20},
        {IRON: 50},
    ]


def test_event_transition_requires_one_aligned_occurrence_offset() -> None:
    circuit = Circuit("event_offset_mismatch")
    event = circuit.signal_event("event", guaranteed_min_separation=1)
    accumulator = circuit.accumulator("accumulator")
    mixed = event.step(1) + event.step(2)

    with pytest.raises(EventCausalityError, match="one logical occurrence offset"):
        accumulator.add(mixed)
