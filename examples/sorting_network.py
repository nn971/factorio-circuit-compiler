"""Per-tick Batcher bitonic sorting network with N = 2**power scalar inputs."""

from __future__ import annotations

import argparse

from factorio_circuit import Circuit, Expr, compile_circuit


def _require_power(power: int) -> int:
    if isinstance(power, bool) or not isinstance(power, int) or power < 1:
        raise ValueError("power must be a positive integer")
    return 1 << power


def _compare_exchange(left: Expr, right: Expr, *, ascending: bool) -> tuple[Expr, Expr]:
    """Return the pair ordered in the requested direction."""

    swap = left > right if ascending else left < right
    return swap.select(right, left), swap.select(left, right)


def build_sorting_circuit(power: int) -> Circuit:
    """Build an N-input, N-output bitonic sorting network for N = 2**power."""

    size = _require_power(power)
    circuit = Circuit(f"bitonic_sort_{size}")
    values = [circuit.input(f"x{index}") for index in range(size)]

    # Iterative Batcher bitonic network. For each pair (i, i ^ distance), the
    # direction is selected solely from the static wire index, so the generated
    # dataflow graph is fixed and can accept a fresh vector every tick.
    width = 2
    while width <= size:
        distance = width // 2
        while distance:
            for left_index in range(size):
                right_index = left_index ^ distance
                if right_index <= left_index:
                    continue
                ascending = (left_index & width) == 0
                left = values[left_index]
                right = values[right_index]
                values[left_index], values[right_index] = _compare_exchange(
                    left,
                    right,
                    ascending=ascending,
                )
            distance //= 2
        width *= 2

    for index, value in enumerate(values):
        circuit.output(f"y{index}", value)
    return circuit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "power",
        nargs="?",
        type=int,
        default=3,
        help="build a sorter with N = 2**power inputs (default: 3, so N=8)",
    )
    parser.add_argument(
        "--no-optimize",
        action="store_true",
        help="disable semantic/packing optimization before physical synthesis",
    )
    args = parser.parse_args()

    circuit = build_sorting_circuit(args.power)
    result = compile_circuit(circuit, optimize=not args.no_optimize)
    size = 1 << args.power
    phases = result.physical_circuit.output_phases
    print(f"bitonic sorter: N={size}, combinators={result.physical_circuit.combinator_count}")
    print(f"output phases: min=+{min(phases)}, max=+{max(phases)}")
    print(result.blueprint_string)


if __name__ == "__main__":
    main()
