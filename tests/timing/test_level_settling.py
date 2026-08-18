import pytest

from factorio_circuit import Circuit, SignalId, compile_circuit
from factorio_circuit.compiler import lower_to_abstract_physical
from factorio_circuit.ir.abstract_physical import ArithmeticCombinator
from factorio_circuit.lowering.settling import ValidityWindow
from factorio_circuit.simulate.physical import simulate_stream as simulate_physical_stream
from factorio_circuit.simulate.semantic import simulate_stream as simulate_semantic_stream

VALUE = SignalId("virtual", "signal-A")


def _delay_count(circuit: object, description: str) -> int:
    entities = getattr(circuit, "entities")
    return sum(
        isinstance(entity, ArithmeticCombinator) and entity.description == description
        for entity in entities
    )


def _assert_periodic_stream(
    source: Circuit,
    logical_stream: list[dict[str, object]],
    *,
    optimize: bool,
    between_rows: list[dict[str, object]] | None = None,
) -> None:
    """Compare one-clock-domain semantics at ``k*P + output.phase`` physical observations."""

    result = compile_circuit(source, optimize=optimize)
    period = result.state_timing.uniform_period
    assert period is not None and period > 1

    expected = simulate_semantic_stream(result.semantic_ir, logical_stream)
    physical_stream: list[dict[str, object]] = []
    noise = between_rows or [{}]
    for index, row in enumerate(logical_stream):
        physical_stream.append(row)
        for offset in range(1, period):
            physical_stream.append(noise[(index * period + offset) % len(noise)])

    observations = simulate_physical_stream(
        result.physical_circuit,
        physical_stream,
        flush_ticks=max(result.physical_circuit.output_phases, default=0) + period,
    )
    for logical_tick, expected_row in enumerate(expected):
        for output_index, port in enumerate(result.physical_circuit.outputs):
            physical_tick = logical_tick * period + port.phase
            assert observations[physical_tick][output_index] == expected_row[output_index]


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


def _held_output_circuit() -> Circuit:
    c = Circuit("settling_hold_output")
    one = c.constant_signals({VALUE: 1})
    memory = c.freeze("memory")

    old = memory.sample()
    deep = (old + one) + one
    memory.set(old + deep, when=1)

    c.step(1)
    current = memory.sample()
    frame = current + (current + one)
    c.output("frame", frame)
    return c


def _fresh_input_skew_circuit() -> Circuit:
    """Multicycle recurrence with unequal paths from one fresh external Level sample."""

    c = Circuit("fresh_input_path_skew")
    data = c.signals("data")
    one = c.constant_signals({VALUE: 1})
    memory = c.freeze("memory")

    old = memory.sample()
    deep = (data + one) + one
    mixed = data + deep
    # Referencing old state makes this a genuine synchronous feedback region (P > 1), while
    # ``data`` still reaches ``mixed`` along both a zero-depth and a two-combinator path.  The short
    # fresh-input path must be transported exactly; unlike ``old``, it cannot borrow the whole state
    # period as a validity window.
    memory.set(old + mixed, when=1)

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


@pytest.mark.parametrize("optimize", [False, True])
def test_held_state_settling_matches_logical_recurrence(optimize: bool) -> None:
    _assert_periodic_stream(
        _stable_feedback_circuit(),
        [{} for _ in range(8)],
        optimize=optimize,
    )


def test_derived_level_output_gets_hold_only_at_observation_boundary() -> None:
    result = lower_to_abstract_physical(_held_output_circuit(), optimize=False)
    descriptions = {
        getattr(entity, "description", None) for entity in result.abstract_physical.entities
    }

    assert _delay_count(result.abstract_physical, "vector phase alignment delay") == 0
    assert "Level HOLD: capture vector output at logical boundary" in descriptions
    assert "Level HOLD: retain vector output between logical boundaries" in descriptions


@pytest.mark.parametrize("optimize", [False, True])
def test_derived_level_output_is_dense_and_coherent_between_activations(optimize: bool) -> None:
    result = compile_circuit(_held_output_circuit(), optimize=optimize)
    period = result.state_timing.uniform_period
    assert period is not None and period > 1

    logical_stream = [{} for _ in range(6)]
    expected = simulate_semantic_stream(result.semantic_ir, logical_stream)
    physical_stream = [{} for _ in range(len(logical_stream) * period)]
    output_phase = result.physical_circuit.outputs[0].phase
    observations = simulate_physical_stream(
        result.physical_circuit,
        physical_stream,
        flush_ticks=output_phase + period,
    )

    for logical_tick, expected_row in enumerate(expected):
        start = logical_tick * period + output_phase
        for physical_tick in range(start, start + period):
            assert observations[physical_tick][0] == expected_row[0]


def test_fresh_level_input_still_uses_exact_delay_for_path_skew() -> None:
    result = lower_to_abstract_physical(_fresh_input_skew_circuit(), optimize=False)

    period = result.state_timing.uniform_period
    assert period is not None and period > 1
    # ``data`` may change on the next physical tick. The short direct path must therefore be
    # transported to meet the deeper path; treating it like held state would change which logical
    # input occurrence is consumed.
    assert _delay_count(result.abstract_physical, "vector phase alignment delay") >= 2


@pytest.mark.parametrize("optimize", [False, True])
def test_fresh_level_input_keeps_exact_stream_semantics(optimize: bool) -> None:
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
    # Intermediate physical ticks deliberately carry unrelated data. Correct lowering must consume
    # the boundary sample, not whatever happens to be present when the longest path settles.
    noise = [
        {"data": {VALUE: 1000}},
        {"data": {VALUE: -777}},
        {"data": {VALUE: 42}},
    ]

    _assert_periodic_stream(
        _fresh_input_skew_circuit(),
        stream,
        optimize=optimize,
        between_rows=noise,
    )
