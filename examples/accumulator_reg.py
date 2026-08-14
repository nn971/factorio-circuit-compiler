from factorio_circuit import Circuit, compile_circuit

circuit = Circuit("accumulator")
data = circuit.signals("data")
clear = circuit.input("clear")
memory = circuit.accumulator("memory")

memory.add(data)
memory.clear(when=clear)
circuit.step(1)
circuit.output("memory", memory.sample())


if __name__ == "__main__":
    result = compile_circuit(circuit, optimize=False)
    print(result.blueprint_string)
