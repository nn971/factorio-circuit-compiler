import pytest

from factorio_circuit import compile_circuit
from factorio_circuit.simulate.compare import assert_same_stream
from factorio_circuit.simulate.semantic import simulate_stream as simulate_semantic_stream

from ..support.circuits import switchable_fibonacci


def test_switchable_fibonacci_semantic_sequence() -> None:
    circuit = switchable_fibonacci()
    module = circuit.build()
    stream: list[dict[str, object]] = [{"on": 1} for _ in range(12)]
    values = [row[0] for row in simulate_semantic_stream(module, stream)]
    assert values == [1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144]


def test_switchable_fibonacci_holds_while_off_and_resumes() -> None:
    circuit = switchable_fibonacci()
    module = circuit.build()
    switches = [1, 1, 1, 0, 0, 0, 1, 1, 1, 1]
    stream: list[dict[str, object]] = [{"on": value} for value in switches]
    values = [row[0] for row in simulate_semantic_stream(module, stream)]
    assert values == [1, 1, 2, 2, 2, 2, 3, 5, 8, 13]


def test_switchable_fibonacci_state_cycle_has_equal_phases() -> None:
    result = compile_circuit(switchable_fibonacci(), optimize=False)
    phases = {item.register.name: item.state_phase for item in result.state_timing.registers}
    assert phases["fib_a"] == phases["fib_b"]
    assert phases["fib_a"] >= 1


@pytest.mark.parametrize("optimize", [False, True])
@pytest.mark.parametrize(
    "switches",
    [
        [1] * 12,
        [0, 0, 1, 1, 1, 1, 0, 0, 1, 1, 1, 1, 1, 0, 1],
    ],
    ids=["continuous-on", "hold-and-resume"],
)
def test_switchable_fibonacci_matches_physical_stream(
    optimize: bool, switches: list[int]
) -> None:
    result = compile_circuit(switchable_fibonacci(), optimize=optimize)
    stream: list[dict[str, object]] = [{"on": value} for value in switches]
    assert_same_stream(result.semantic_ir, result.physical_circuit, stream)
