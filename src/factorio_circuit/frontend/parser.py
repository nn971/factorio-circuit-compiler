"""Compatibility entry point for the removed AST frontend.

The canonical frontend now executes ordinary Python elaboration over symbolic ``Circuit`` objects.
``parse_circuit`` is retained only as a narrow migration helper for already-built circuits/modules.
"""

from __future__ import annotations

from factorio_circuit.frontend.symbolic import Circuit
from factorio_circuit.ir.semantic import CircuitModule


class CircuitSyntaxError(ValueError):
    """Compatibility error name from the former restricted-Python parser."""


def parse_circuit(source: Circuit | CircuitModule) -> CircuitModule:
    if isinstance(source, CircuitModule):
        return source
    if isinstance(source, Circuit):
        return source.build()
    raise CircuitSyntaxError(
        "the @circuit AST frontend has been removed; construct a Circuit with symbolic inputs, "
        "expressions, state objects, and explicit outputs"
    )
