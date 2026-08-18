from factorio_circuit import Circuit, SignalId, compile_circuit
from factorio_circuit.compiler import lower_to_abstract_physical
from factorio_circuit.ir.abstract_physical import ArithmeticCombinator
from factorio_circuit.lowering.settling import ValidityWindow
from factorio_circuit.simulate.compare import assert_same_stream

VALUE = SignalId("virtual", "signal-A")


def _delay_count(circuit: object, description: str) -> int:
    entities = getattr(circuit, "entities")
    return sum(
        isinstance(entity, ArithmeticCombinator) and entity.description == description
        for entity in entities
    )


def _stable_feedback_circuit() -> Circuit:
    c = Circuit("stable_feedback_settling")
    one = c.constant_signals({VALUE: 1})
    memory = c.freeze("memory")

    old = memory.sample()
    deep = (old + one) + one
    next_value = old + deep
    memory.set(next_value, when=1)

    c.step(1)
    c.output("value", memory.sample())
    return c


def _fresh_input_skew_circuit() -> Circuit:
    c = Circuit("fresh_input_path_skew")
    data = c.signals("data")
    one = c.constant_signals({VALUE: 1})
    memory = c.accumulator("memory")

    deep = (data + one) + one
    mixed = data + deep
    memory.add(mixed)

    c.step(1)
    c.output("value", memory.sample())
    return c


def test_validity_window_is_half_open_and_shiftable() -> None:
    window = ValidityWindow(3, 8)

    assert not window.contains(2)
    assert window.contains(3)
    assert window.contains(7)
    assert not window.contains(8)
    assert window.from_phase(5) == ValidityWindow(5, 8)
    assert window.shift(4) == ValidityWindow(7, 12)
    assert window.intersect(ValidityWindow(6, 10)) == ValidityWindow(6, 8)
    assert ValidityWindow(2, None).contains(10_000)


def test_held_state_removes_internal_vector_phase_padding() -> None:
    result = lower_to_abstract_physical(_stable_feedback_circuit(), optimize=False)

    assert result.state_timing.uniform_period is not None
    assert result.state_timing.uniform_period > 1
    assert _delay_count(result.abstract_physical, "vector phase alignment delay") == 0

    # Startup is an intentional physical-time guard for the modulo clock and must not be mistaken
    # for ordinary Level persistence.
    assert _delay_count(result.abstract_physical, "phase alignment delay") > 0


def test_held_state_settling_matches_logical_recurrence() -> None:
    result = compile_circuit(_stable_feedback_circuit(), optimize=False)

    assert_same_stream(result.semantic_ir, result.physical_circuit, [{} for _ in range(8)])


def test_fresh_level_input_still_uses_exact_delay_for_path_skew() -> None:
    result = lower_to_abstract_physical(_fresh_input_skew_circuit(), optimize=False)

    # ``data`` may change on the next physical tick. The short direct path must therefore be
    # transported to meet the deeper path; treating it like held state would change which logical
    # input occurrence is consumed.
    assert _delay_count(result.abstract_physical, "vector phase alignment delay") >= 2


def test_fresh_level_input_keeps_exact_stream_semantics() -> None:
    result = compile_circuit(_fresh_input_skew_circuit(), optimize=False)
    stream = [
        {"data": {VALUE: 1}},
        {"data": {VALUE: 3}},
        {"data": {VALUE: -2}},
        {"data": {VALUE: 9}},
        {"data": {}},
        {"data": {VALUE: 4}},
        {"data": {VALUE: 11}},
        {"data": {}},
    ]

    assert_same_stream(result.semantic_ir, result.physical_circuit, stream)
