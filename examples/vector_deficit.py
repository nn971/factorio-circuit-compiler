from factorio_circuit import compile_circuit
from factorio_circuit.frontend import Circuit

circuit = Circuit("vector_deficit")
required = circuit.signals("required")
stock = circuit.signals("stock")
enabled = circuit.input("enabled")

missing = (required - stock).positive()
request = missing.max()
has_missing = missing.any()

store_request = enabled * has_missing
pending_request = circuit.freeze("pending_request")
pending_request.set(request, when=store_request)

circuit.output("missing", missing)
circuit.output("request", request)
circuit.output("has_missing", has_missing)
circuit.output("enabled_missing", missing.gate(enabled))

circuit.step(1)
circuit.output("pending_request", pending_request.sample())

result = compile_circuit(circuit)
print(result.blueprint_string)
