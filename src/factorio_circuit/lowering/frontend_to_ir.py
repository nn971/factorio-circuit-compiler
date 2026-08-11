"""Symbolic-frontend to semantic-IR lowering."""

from factorio_circuit.frontend.symbolic import Circuit
from factorio_circuit.ir.semantic import CircuitModule


def lower_frontend(source: Circuit | CircuitModule) -> CircuitModule:
    if isinstance(source, CircuitModule):
        return source
    if isinstance(source, Circuit):
        return source.build()
    raise TypeError(
        "compile_circuit() expects a symbolic Circuit; decorated Python functions are no longer "
        "the circuit frontend"
    )
