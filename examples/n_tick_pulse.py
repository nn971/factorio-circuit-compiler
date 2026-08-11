"""Representative temporal circuit: stretch a one-tick trigger to N physical output ticks."""

from factorio_circuit import Circuit, compile_circuit

N = 5
circuit = Circuit(f"pulse_{N}")
trigger = circuit.input("trigger")
pulse = trigger != 0
for _ in range(1, N):
    circuit.tick(1)
    pulse = pulse | (trigger.sample() != 0)
circuit.output("pulse", pulse)

if __name__ == "__main__":
    result = compile_circuit(circuit, optimize=False)
    print(f"output phase: +{result.physical_circuit.outputs[0].phase}")
    print(result.blueprint_string)
