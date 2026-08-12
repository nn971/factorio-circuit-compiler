"""Per-tick fast Walsh-Hadamard transform with N = 2**power scalar inputs."""

from __future__ import annotations

import argparse

from factorio_circuit import Circuit, compile_circuit


def _require_power(power: int) -> int:
    if isinstance(power, bool) or not isinstance(power, int) or power < 1:
        raise ValueError("power must be a positive integer")
    return 1 << power


def build_wht_circuit(power: int) -> Circuit:
    """Build an unnormalized N-point Walsh-Hadamard transform for N = 2**power."""

    size = _require_power(power)
    circuit = Circuit(f"wht_{size}")
    values = [circuit.input(f"x{index}") for index in range(size)]

    # Sylvester-order fast Walsh-Hadamard transform. Every butterfly maps
    # (a, b) -> (a + b, a - b); all butterflies in one stage are independent.
    stride = 1
    while stride < size:
        block = stride * 2
        for start in range(0, size, block):
            for offset in range(stride):
                left_index = start + offset
                right_index = left_index + stride
                left = values[left_index]
                right = values[right_index]
                values[left_index] = left + right
                values[right_index] = left - right
        stride *= 2

    for index, value in enumerate(values):
        circuit.output(f"y{index}", value)
    return circuit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "power",
        nargs="?",
        type=int,
        default=5,
        help="build a WHT with N = 2**power inputs (default: 5, so N=32)",
    )
    parser.add_argument(
        "--no-optimize",
        action="store_true",
        help="disable semantic/packing optimization before physical synthesis",
    )
    args = parser.parse_args()

    circuit = build_wht_circuit(args.power)
    result = compile_circuit(circuit, optimize=not args.no_optimize)
    size = 1 << args.power
    phases = result.physical_circuit.output_phases
    combinators = result.physical_circuit.combinator_count
    print(f"Walsh-Hadamard transform: N={size}, combinators={combinators}")
    print(f"output phases: min=+{min(phases)}, max=+{max(phases)}")
    print(result.blueprint_string)


if __name__ == "__main__":
    main()
