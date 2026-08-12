from collections.abc import Callable

import pytest

from examples.sorting_network import build_sorting_circuit
from examples.walsh_hadamard import build_wht_circuit
from factorio_circuit import Circuit, compile_circuit
from factorio_circuit.simulate.semantic import evaluate


@pytest.mark.parametrize(
    ("power", "values"),
    [
        (1, [7, -2]),
        (2, [9, -1, 4, 4]),
        (3, [8, 2, 7, 1, 5, 3, 6, 4]),
    ],
)
def test_bitonic_sorting_network_matches_sorted(power: int, values: list[int]) -> None:
    circuit = build_sorting_circuit(power)
    inputs = {f"x{index}": value for index, value in enumerate(values)}

    assert evaluate(circuit.build(), inputs) == tuple(sorted(values))


@pytest.mark.parametrize(
    ("power", "values"),
    [
        (1, [3, 5]),
        (2, [3, 3, -3, -3]),
        (3, [4, 1, -2, 7, 0, 3, 5, -1]),
    ],
)
def test_wht_matches_sylvester_hadamard_matrix(power: int, values: list[int]) -> None:
    circuit = build_wht_circuit(power)
    inputs = {f"x{index}": value for index, value in enumerate(values)}
    size = 1 << power
    expected = tuple(
        sum(
            value if (row & column).bit_count() % 2 == 0 else -value
            for column, value in enumerate(values)
        )
        for row in range(size)
    )

    assert evaluate(circuit.build(), inputs) == expected


@pytest.mark.parametrize(
    ("builder", "power"),
    [
        (build_sorting_circuit, 2),
        (build_wht_circuit, 3),
    ],
)
def test_layout_benchmark_examples_compile(
    builder: Callable[[int], Circuit],
    power: int,
) -> None:
    result = compile_circuit(builder(power))
    size = 1 << power

    assert len(result.physical_circuit.outputs) == size
    assert result.physical_circuit.combinator_count > 0
    assert result.blueprint_string.startswith("0")


@pytest.mark.parametrize("builder", [build_sorting_circuit, build_wht_circuit])
def test_layout_benchmark_examples_require_positive_power(
    builder: Callable[[int], Circuit],
) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        builder(0)
