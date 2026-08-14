from factorio_circuit import compile_circuit
from factorio_circuit.frontend import Circuit

circuit = Circuit("state_vector_predicate")
request = circuit.signals("request")
enqueue = circuit.input("enqueue")
accept = circuit.input("accept")

pending = circuit.freeze("pending")
worker = circuit.freeze("worker")

old_pending = pending.sample()
pending.set(request, when=enqueue)
worker.set(old_pending, when=accept * old_pending.any())

circuit.step(1)
circuit.output("pending", pending.sample())
circuit.output("worker", worker.sample())

result = compile_circuit(circuit)
print(result.blueprint_string)
