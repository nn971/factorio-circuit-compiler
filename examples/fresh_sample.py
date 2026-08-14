"""Fresh external sampling across logical steps without exposing physical execution ticks."""

from factorio_circuit import Circuit, compile_circuit

circuit = Circuit("fresh_sample")
x = circuit.input("x")
x0 = x.sample()

circuit.step(3)
x3 = x.sample()

circuit.output("sum", x0 + x3)


if __name__ == "__main__":
    result = compile_circuit(circuit, optimize=False)
    print("output phase:", result.physical_circuit.outputs[0].phase)
    print(result.blueprint_string)
