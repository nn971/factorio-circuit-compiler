"""Semantic optimizer orchestration."""

from factorio_circuit.ir.output import preserve_output_materializations
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

    normalized = preserve_output_materializations(normalize_module(module), module.output)
    return optimize_normalized_semantic(normalized)


def optimize_normalized_semantic(module: CircuitModule) -> CircuitModule:
    """Run optimization after the module has crossed the canonical Level boundary."""

    reject_event_module(module)
    validate_canonical_module(module)
    source_output = module.output
    module = simplify_module(module)
    module = eliminate_common_subexpressions(module)
    module = eliminate_dead_code(module)
    # Conservative optimizer rewrites use the legacy constructors for compatibility.  Re-enter the
    # single canonical boundary so timing and physical consumers never see an unannotated Level
    # expression, and repeated optimization remains idempotent. Materialization is an external
    # boundary contract, so preserve it by output position across those expression rewrites.
    normalized = preserve_output_materializations(normalize_module(module), source_output)
    validate_canonical_module(normalized)
    return normalized
