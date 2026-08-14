"""Semantic optimizer orchestration."""

from factorio_circuit.ir.semantic import CircuitModule, reject_event_module
from factorio_circuit.optimize.common_subexpr import eliminate_common_subexpressions
from factorio_circuit.optimize.dead_code import eliminate_dead_code
from factorio_circuit.optimize.simplify import simplify_module


def optimize_semantic(module: CircuitModule) -> CircuitModule:
    """Run the conservative semantic simplification/CSE/DCE pipeline."""

    reject_event_module(module)
    module = simplify_module(module)
    module = eliminate_common_subexpressions(module)
    module = eliminate_dead_code(module)
    return module
