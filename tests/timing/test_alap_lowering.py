import pytest

from factorio_circuit import Circuit, SignalId, compile_circuit
from factorio_circuit.compiler import lower_to_abstract_physical
from factorio_circuit.ir.abstract_physical import ArithmeticCombinator
from factorio_circuit.simulate.physical import simulate_stream as simulate_physical_stream
from factorio_circuit.simulate.semantic import simulate_stream as simulate_semantic_stream

VALUE = SignalId("virtual", "signal-A")
OTHER = SignalId("virtual", "signal-B")


def _delay_count(circuit: object, description: str) -> int:
    entities = circuit.entities
    return sum(
        isinstance(entity, ArithmeticCombinator) and entity.description == description
        for entity in entities
    )


def _external_fanout_circuit() -> Circuit:
    """One fresh vector snapshot feeds cheap branches beside a deep state recurrence."""

    c = Circuit("alap_external_snapshot_fanout")
    data = c.signals("data")
    one = c.constant_signals({VALUE: 1})
    memory = c.accumulator("memory")

    # Force a nontrivial synchronous settling budget from genuine state recurrence.  ALAP can place
    # this whole chain across the period for free because the old state token is held throughout it.
    old = memory.sample()
    deep = old
    for _ in range(5):
        deep = deep + one
    memory.add(deep)

    # These are the important branches.  ASAP lowering computes both at phase 1 and delays their
    # distinct results to the transition boundary.  ALAP should instead transport ``data`` once to
    # the final input phase, then emit both cheap operations there.
    memory.add(data + one)
    memory.add(data * 2)

    c.step(1)
    c.output("value", memory.sample())
    return c


def _external_lane_fanout_circuit() -> Circuit:
    """Different scalar lanes of one vector snapshot should share vector transport first."""

    c = Circuit("alap_external_lane_fanout")
    data = c.signals("data")
    one = c.constant_signals({VALUE: 1})
    memory = c.accumulator("memory")

    old = memory.sample()
    deep = old
    for _ in range(5):
        deep = deep + one
    memory.add(deep)

    active_a = data.signal(VALUE) != 0
    active_b = data.signal(OTHER) != 0
    memory.add(one * active_a)
    memory.add(one * active_b)

    c.step(1)
    c.output("value", memory.sample())
    return c


def test_alap_external_fanout_uses_one_shared_vector_trunk() -> None:
    result = lower_to_abstract_physical(_external_fanout_circuit(), optimize=False)
    timing = result.state_timing.registers[0]

    assert timing.period > 2
    # Both one-stage cheap branches consume the same phase-0 external snapshot one tick before the
    # state transition input.  Shared vector-delay prefixes therefore cost one trunk, not one chain
    # per branch.  State-derived paths need no vector padding under the settling proof.
    expected_trunk = timing.transition_input_phase - 1
    assert expected_trunk > 0
    assert _delay_count(result.abstract_physical, "vector phase alignment delay") == expected_trunk


def test_alap_lane_reads_share_vector_transport_before_projection() -> None:
    result = lower_to_abstract_physical(_external_lane_fanout_circuit(), optimize=False)
    timing = result.state_timing.registers[0]

    # Each lane comparison is one stage and each vector-scalar gate is one more stage, so the whole
    # external vector snapshot is transported to T-2 exactly once before A/B are projected.  Without
    # this rule the two concrete lanes would grow independent scalar delay chains.
    expected_trunk = timing.transition_input_phase - 2
    assert expected_trunk > 0
    assert _delay_count(result.abstract_physical, "vector phase alignment delay") == expected_trunk


@pytest.mark.parametrize("optimize", [False, True])
def test_alap_preserves_one_external_snapshot_across_fanout(optimize: bool) -> None:
    source = _external_fanout_circuit()
    result = compile_circuit(source, optimize=optimize)
    period = result.state_timing.uniform_period
    assert period is not None and period > 2

    logical_stream = [
        {"data": {VALUE: 1, OTHER: 2}},
        {"data": {VALUE: 3, OTHER: -1}},
        {"data": {VALUE: -2, OTHER: 4}},
        {"data": {}},
        {"data": {VALUE: 7}},
        {"data": {OTHER: 5}},
    ]
    expected = simulate_semantic_stream(result.semantic_ir, logical_stream)

    # Deliberately mutate the physical input between logical boundaries.  Correct ALAP scheduling
    # must move computation, not sampling: all fanout branches still consume the boundary snapshot.
    noise = [
        {"data": {VALUE: 1000, OTHER: 1000}},
        {"data": {VALUE: -777, OTHER: 13}},
        {"data": {VALUE: 42, OTHER: -99}},
    ]
    physical_stream: list[dict[str, object]] = []
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
