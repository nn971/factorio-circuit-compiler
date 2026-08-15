"""Semantic optimizer orchestration."""

from factorio_circuit.ir.semantic import (
    CircuitModule,
    reject_event_module,
    validate_canonical_module,
)
from factorio_circuit.lowering.frontend_to_ir import normalize_module
from factorio_circuit.optimize.common_subexpr import eliminate_common_subexpressions
from factorio_circuit.optimize.dead_code import eliminate_dead_code
from factorio_circuit.optimize.simplify import simplify_module


def optimize_semantic(module: CircuitModule) -> CircuitModule:
    """Compatibility wrapper for optimizing a public legacy or canonical module."""

    return optimize_normalized_semantic(normalize_module(module))


def optimize_normalized_semantic(module: CircuitModule) -> CircuitModule:
    """Run optimization after the module has crossed the canonical Level boundary."""

    reject_event_module(module)
    validate_canonical_module(module)
    module = simplify_module(module)
    module = eliminate_common_subexpressions(module)
    module = eliminate_dead_code(module)
    # Conservative optimizer rewrites use the legacy constructors for compatibility.  Re-enter the
    # single canonical boundary so timing and physical consumers never see an unannotated Level
    # expression, and repeated optimization remains idempotent.
    normalized = normalize_module(module)
    validate_canonical_module(normalized)
    return normalized
