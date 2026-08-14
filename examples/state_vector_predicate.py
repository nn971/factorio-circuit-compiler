from factorio_circuit import compile_circuit
from factorio_circuit.frontend import Circuit

circuit = Circuit("state_vector_predicate")
request = circuit.signals("request")
enqueue = circuit.input("enqueue")
accept = circuit.input("accept")

pending = circuit.freeze("pending")
worker = circuit.freeze("worker")

# Read the previous pending request, then use its runtime-open occupancy as ordinary
# scalar control for another state transition.
old_pending = pending.value
pending.set(request, when=enqueue)
worker.set(old_pending, when=accept * old_pending.any())

circuit.tick(1)
circuit.output("pending", pending.value)
circuit.output("worker", worker.value)

result = compile_circuit(circuit)
print(result.blueprint_string)
