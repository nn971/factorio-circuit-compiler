import pytest

from factorio_circuit import compile_circuit
from factorio_circuit.simulate.compare import assert_same_stream
from factorio_circuit.simulate.physical import simulate_stream

from ..support.circuits import n_tick_pulse_generator


@pytest.mark.parametrize("length", [1, 2, 3, 5, 8])
def test_n_tick_pulse_generator_has_exact_physical_width(length: int) -> None:
    result = compile_circuit(n_tick_pulse_generator(length), optimize=False)
    trigger_tick = 6
    stream: list[dict[str, object]] = [
        {"trigger": int(tick == trigger_tick)} for tick in range(trigger_tick + length + 6)
    ]

    # First establish the semantic/physical phase correspondence.
    assert_same_stream(result.semantic_ir, result.physical_circuit, stream)

    observations = simulate_stream(result.physical_circuit, stream)
    high_ticks = [tick for tick, row in enumerate(observations) if row[0] != 0]
    assert len(high_ticks) == length
    assert high_ticks == list(range(high_ticks[0], high_ticks[0] + length))
    assert high_ticks[0] > trigger_tick


def test_pulse_generator_phase_grows_with_requested_window() -> None:
    phases = [
        compile_circuit(n_tick_pulse_generator(length), optimize=False)
        .physical_circuit.outputs[0]
        .phase
        for length in (1, 2, 4, 8)
    ]
    assert phases == sorted(phases)
    assert len(set(phases)) == len(phases)
