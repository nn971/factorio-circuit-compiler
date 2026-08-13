"""Public compiler orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from factorio_circuit.analysis.state_timing import StateTimingPlan, analyze_state_timing
from factorio_circuit.blueprint.layout_encode import (
    encode_layout_blueprint_string,
    layout_to_blueprint_json,
)
from factorio_circuit.blueprint.routing import DEFAULT_SAFE_WIRE_SPAN
from factorio_circuit.frontend.symbolic import Circuit
from factorio_circuit.ir.abstract_physical import AbstractPhysicalCircuit
from factorio_circuit.ir.physical import PhysicalCircuit
from factorio_circuit.ir.semantic import CircuitModule
from factorio_circuit.lowering.frontend_to_ir import lower_frontend
from factorio_circuit.lowering.ir_to_abstract_physical import lower_abstract_physical
from factorio_circuit.optimize.pipeline import optimize_semantic
from factorio_circuit.synthesis.layout import Layout
from factorio_circuit.synthesis.physical import synthesize_layout
from factorio_circuit.synthesis.placement import PlacementOptions


@dataclass(frozen=True, slots=True)
class CompilationResult:
    """Result of the canonical abstract-physical compilation pipeline."""

    semantic_ir: CircuitModule
    optimized_ir: CircuitModule
    state_timing: StateTimingPlan
    abstract_physical: AbstractPhysicalCircuit
    layout: Layout
    naive_physical: PhysicalCircuit
    blueprint_json: dict[str, object]
    blueprint_string: str

    @property
    def physical_circuit(self) -> PhysicalCircuit:
        """Concrete circuit embedded in the final layout, useful for simulation."""

        return self.layout.circuit

    @property
    def combinators_saved(self) -> int:
        return self.naive_physical.combinator_count - self.physical_circuit.combinator_count


# Compatibility name retained while callers migrate to ``CompilationResult``.
AbstractCompilationResult = CompilationResult


def compile_circuit(
    source: Circuit | CircuitModule,
    *,
    optimize: bool = True,
    blueprint_safe_wire_span: float = DEFAULT_SAFE_WIRE_SPAN,
    placement: PlacementOptions | None = None,
) -> CompilationResult:
    """Compile through Abstract Physical IR, physical synthesis, and final Layout."""

    semantic = lower_frontend(source)
    optimized_semantic = optimize_semantic(semantic) if optimize else semantic
    state_timing = analyze_state_timing(optimized_semantic)

    abstract_physical = lower_abstract_physical(
        optimized_semantic,
        enable_packing=optimize,
        state_timing=state_timing,
    )
    layout = synthesize_layout(
        abstract_physical,
        safe_wire_span=blueprint_safe_wire_span,
        placement=placement,
    )

    if optimize:
        naive_abstract = lower_abstract_physical(
            optimized_semantic,
            enable_packing=False,
            state_timing=state_timing,
        )
        naive_physical = synthesize_layout(
            naive_abstract,
            safe_wire_span=blueprint_safe_wire_span,
            placement=placement,
        ).circuit
    else:
        naive_physical = layout.circuit

    return CompilationResult(
        semantic_ir=semantic,
        optimized_ir=optimized_semantic,
        state_timing=state_timing,
        abstract_physical=abstract_physical,
        layout=layout,
        naive_physical=naive_physical,
        blueprint_json=layout_to_blueprint_json(layout),
        blueprint_string=encode_layout_blueprint_string(layout),
    )


def compile_abstract_circuit(
    source: Circuit | CircuitModule,
    *,
    optimize: bool = True,
    blueprint_safe_wire_span: float = DEFAULT_SAFE_WIRE_SPAN,
    placement: PlacementOptions | None = None,
) -> AbstractCompilationResult:
    """Compatibility alias for :func:`compile_circuit`."""

    return compile_circuit(
        source,
        optimize=optimize,
        blueprint_safe_wire_span=blueprint_safe_wire_span,
        placement=placement,
    )
