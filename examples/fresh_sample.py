"""Flow-local logical reindexing without exposing physical execution ticks."""

from factorio_circuit import Circuit, compile_circuit

circuit = Circuit("fresh_sample")
x = circuit.input("x")
x0 = x.sample()
x3 = x0.step(3)

# Reindexing one flow leaves the compatibility cursor untouched.
assert circuit.now.offset == 0
circuit.output("sum", x0 + x3)


if __name__ == "__main__":
    result = compile_circuit(circuit, optimize=False)
    print("output phase:", result.physical_circuit.outputs[0].phase)
    print(result.blueprint_string)
