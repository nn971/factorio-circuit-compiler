"""Dead-code elimination by rebuilding scalar operations from observable roots."""

from factorio_circuit.ir.semantic import CircuitModule, reachable_operations


def eliminate_dead_code(module: CircuitModule) -> CircuitModule:
    return CircuitModule(
        module.name,
        module.inputs,
        reachable_operations(module),
        module.output,
        module.vector_inputs,
        module.state_registers,
        module.state_operations,
        module.event_inputs,
        module.event_state_operations,
        module.sample_on_crossings,
    )
