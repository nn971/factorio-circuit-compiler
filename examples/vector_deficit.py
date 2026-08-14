from factorio_circuit import compile_circuit
from factorio_circuit.frontend import Circuit

circuit = Circuit("vector_deficit")
required = circuit.signals("required")
stock = circuit.signals("stock")
enabled = circuit.input("enabled")

# Compute the positive shortage vector, then choose one request to work on.
missing = (required - stock).positive()
request = missing.max()
has_missing = missing.any()

# Keep the selected one-lane request once scheduling is enabled.  Both the data
# and the write condition are derived from runtime-open vector expressions.
store_request = enabled * has_missing
pending_request = circuit.freeze("pending_request")
pending_request.set(request, when=store_request)

circuit.output("missing", missing)
circuit.output("request", request)
circuit.output("has_missing", has_missing)
circuit.output("enabled_missing", missing.gate(enabled))

# Read after the state transition to observe the newly stored request.
circuit.tick(1)
circuit.output("pending_request", pending_request.value)

result = compile_circuit(circuit)
print(result.blueprint_string)
