from factorio_circuit import Circuit, compile_circuit

circuit = Circuit("freeze")
data = circuit.signals("data")
set_signal = circuit.input("set_signal")
memory = circuit.freeze("memory")

memory.set(data, when=set_signal)
circuit.step(1)
circuit.output("memory", memory.sample())


if __name__ == "__main__":
    result = compile_circuit(circuit, optimize=False)
    print(result.blueprint_string)
