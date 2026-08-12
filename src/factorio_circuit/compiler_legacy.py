"""Legacy direct-concrete backend retained as a parity/debugging oracle."""

from __future__ import annotations

from dataclasses import dataclass

from factorio_circuit.analysis.state_timing import StateTimingPlan, analyze_state_timing
from factorio_circuit.blueprint.encode import encode_blueprint_string, to_blueprint_json
from factorio_circuit.blueprint.routing import DEFAULT_SAFE_WIRE_SPAN
from factorio_circuit.frontend.symbolic import Circuit
from factorio_circuit.ir.physical import PhysicalCircuit
from factorio_circuit.ir.semantic import CircuitModule
from factorio_circuit.lowering.frontend_to_ir import lower_frontend
from factorio_circuit.lowering.ir_to_physical import lower_naive, lower_with_alu_packing
from factorio_circuit.optimize.pipeline import optimize_semantic


@dataclass(frozen=True, slots=True)
class LegacyCompilationResult:
    semantic_ir: CircuitModule
    optimized_ir: CircuitModule
    state_timing: StateTimingPlan
    naive_physical: PhysicalCircuit
    physical_circuit: PhysicalCircuit
    blueprint_json: dict[str, object]
    blueprint_string: str

    @property
    def combinators_saved(self) -> int:
        return self.naive_physical.combinator_count - self.physical_circuit.combinator_count


def compile_legacy_circuit(
    source: Circuit | CircuitModule,
    *,
    optimize: bool = True,
    blueprint_safe_wire_span: float = DEFAULT_SAFE_WIRE_SPAN,
) -> LegacyCompilationResult:
    """Compile with the pre-Abstract-Physical direct concrete lowerer."""

    semantic = lower_frontend(source)
    optimized_semantic = optimize_semantic(semantic) if optimize else semantic
    state_timing = analyze_state_timing(optimized_semantic)
    naive = lower_naive(optimized_semantic, state_timing=state_timing)
    physical = (
        lower_with_alu_packing(optimized_semantic, state_timing=state_timing) if optimize else naive
    )
    return LegacyCompilationResult(
        semantic_ir=semantic,
        optimized_ir=optimized_semantic,
        state_timing=state_timing,
        naive_physical=naive,
        physical_circuit=physical,
        blueprint_json=to_blueprint_json(
            physical,
            safe_wire_span=blueprint_safe_wire_span,
        ),
        blueprint_string=encode_blueprint_string(
            physical,
            safe_wire_span=blueprint_safe_wire_span,
        ),
    )
