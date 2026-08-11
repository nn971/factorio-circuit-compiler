"""Semantic optimizer orchestration."""

from factorio_circuit.ir.semantic import CircuitModule
from factorio_circuit.optimize.common_subexpr import eliminate_common_subexpressions
from factorio_circuit.optimize.dead_code import eliminate_dead_code
from factorio_circuit.optimize.simplify import simplify_module


def optimize_semantic(module: CircuitModule) -> CircuitModule:
    """Run the conservative Phase-I semantic optimization pipeline."""

    module = simplify_module(module)
    module = eliminate_common_subexpressions(module)
    module = eliminate_dead_code(module)
    return module
