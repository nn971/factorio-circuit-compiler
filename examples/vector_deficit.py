from factorio_circuit import compile_circuit
from factorio_circuit.frontend import Circuit

circuit = Circuit("vector_deficit")
required = circuit.signals("required")
stock = circuit.signals("stock")
enabled = circuit.input("enabled")

missing = (required - stock).positive()
request = missing.max()

circuit.output("missing", missing)
circuit.output("request", request)
circuit.output("has_missing", missing.any())
circuit.output("enabled_missing", missing.gate(enabled))

result = compile_circuit(circuit)
print(result.blueprint_string)
