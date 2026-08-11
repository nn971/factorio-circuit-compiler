"""Combinational DAG + symbolic runtime selection example."""

from factorio_circuit import Circuit, compile_circuit

circuit = Circuit("controller")
a = circuit.input("a")
b = circuit.input("b")
limit = circuit.input("limit")

total = (a + b) * 3
result = (total > limit).select(limit, total)
circuit.output("result", result)


if __name__ == "__main__":
    compiled = compile_circuit(circuit)
    print("combinators:", compiled.physical_circuit.combinator_count)
    print("output phase:", compiled.physical_circuit.outputs[0].phase)
    print(compiled.blueprint_string)
