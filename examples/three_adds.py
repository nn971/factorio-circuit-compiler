"""First packing example: zero-preserving lane-wise multiplication."""

from factorio_circuit import Circuit, compile_circuit

circuit = Circuit("three_multiplies")
a = circuit.input("a")
b = circuit.input("b")
c = circuit.input("c")

circuit.output("x", a * 2)
circuit.output("y", b * 2)
circuit.output("z", c * 2)


if __name__ == "__main__":
    result = compile_circuit(circuit)
    print("naive combinators:", result.naive_physical.combinator_count)
    print("optimized combinators:", result.physical_circuit.combinator_count)
    print(result.blueprint_string)
