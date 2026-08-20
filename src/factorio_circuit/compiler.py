"""Public compiler orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from factorio_circuit.analysis import (
    analyze_clocked_timing,
    analyze_normalized_state_timing,
    validate_event_throughput,
)
from factorio_circuit.analysis.state_timing import StateTimingPlan
from factorio_circuit.blueprint.layout_encode import (
    encode_layout_blueprint_string,
    layout_to_blueprint_json,
)
from factorio_circuit.blueprint.routing import DEFAULT_SAFE_WIRE_SPAN
from factorio_circuit.frontend.symbolic import Circuit
from factorio_circuit.ir.abstract_physical import AbstractPhysicalCircuit
from factorio_circuit.ir.oracle import oracle_sources
from factorio_circuit.ir.output import preserve_output_materializations
from factorio_circuit.ir.physical import PhysicalCircuit
from factorio_circuit.ir.semantic import (
    CircuitModule,
    ReturnValue,
    contains_event_semantics,
    is_vector_expression,
)
from factorio_circuit.lowering.event_accumulator_physical import (
    lower_event_accumulator_physical,
)
from factorio_circuit.lowering.frontend_to_ir import lower_frontend
from factorio_circuit.lowering.open_vector_pipeline import lower_normalized_vectors
from factorio_circuit.optimize.pipeline import optimize_normalized_semantic
from factorio_circuit.oracles import (
    OracleBindingError,
    OracleProvider,
    materialize_oracle_providers,
    validate_oracle_provider_bindings,
)
from factorio_circuit.progress import ProgressCallback, report_progress
from factorio_circuit.synthesis.layout import Layout
from factorio_circuit.synthesis.open_vector import synthesize_vector_layout
from factorio_circuit.synthesis.placement import PlacementOptions, Position


@dataclass(frozen=True, slots=True)
class AbstractPhysicalLoweringResult:
    """Compiler snapshots available immediately before physical synthesis/layout."""

    semantic_ir: CircuitModule
    optimized_ir: CircuitModule
    state_timing: StateTimingPlan
    abstract_physical: AbstractPhysicalCircuit
    clocked: bool


@dataclass(frozen=True, slots=True)
class CompilationResult:
    """Snapshots produced by the canonical compiler pipeline."""

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


def _contains_vector_output(module: CircuitModule) -> bool:
    return any(is_vector_expression(value) for value in module.output.values)


def _source_output(source: Circuit | CircuitModule) -> ReturnValue:
    return source.output if isinstance(source, CircuitModule) else source.build().output


def _lower_level(
    module: CircuitModule,
    *,
    enable_packing: bool,
    state_timing: StateTimingPlan,
) -> AbstractPhysicalCircuit:
    return lower_normalized_vectors(
        module,
        enable_packing=enable_packing,
        state_timing=state_timing,
    )


def _synthesize(
    circuit: AbstractPhysicalCircuit,
    *,
    safe_wire_span: float,
    placement: PlacementOptions | None,
    physical_anchors: Mapping[str, Position] | None,
    progress: ProgressCallback | None,
) -> Layout:
    return synthesize_vector_layout(
        circuit,
        safe_wire_span=safe_wire_span,
        placement=placement,
        anchor_positions=physical_anchors,
        progress=progress,
    )


def lower_to_abstract_physical(
    source: Circuit | CircuitModule,
    *,
    optimize: bool = True,
    progress: ProgressCallback | None = None,
    oracle_providers: Mapping[str, OracleProvider] | None = None,
) -> AbstractPhysicalLoweringResult:
    """Run the canonical compiler pipeline through abstract physical lowering only.

    This is the stable inspection boundary immediately before signal allocation, red/green
    assignment, physical-net coalescing, placement, and routing. It is useful for diagnostics and
    heavyweight benchmark census work that should not need to materialize a final layout.

    Semantic oracles require exact physical-provider coverage. Providers are materialized into the
    abstract physical graph before this function returns, so their entities and unresolved symbolic
    placement requirements participate in the same joint synthesis/layout pass as ordinary
    compiler-generated combinators.
    """

    report_progress(progress, "frontend", detail="elaborating and lowering source program")
    source_output = _source_output(source)
    semantic = preserve_output_materializations(lower_frontend(source), source_output)
    providers = validate_oracle_provider_bindings(semantic, oracle_providers)
    clocked = contains_event_semantics(semantic)

    if clocked:
        if oracle_sources(semantic):
            raise OracleBindingError(
                "physical oracle providers are currently supported for Level modules only"
            )
        optimized_semantic = semantic
        report_progress(progress, "timing", detail="analyzing clocked timing and throughput")
        state_timing = analyze_clocked_timing(optimized_semantic)
        validate_event_throughput(state_timing)
        report_progress(progress, "physical-lowering", detail="lowering Event/state semantics")
        abstract_physical = lower_event_accumulator_physical(
            optimized_semantic,
            state_timing=state_timing,
        )
    else:
        skip_scalar_optimizer = _contains_vector_output(semantic)
        report_progress(progress, "optimization", detail="normalizing semantic expressions")
        optimized_semantic = (
            optimize_normalized_semantic(semantic)
            if optimize and not skip_scalar_optimizer
            else semantic
        )
        optimized_semantic = preserve_output_materializations(optimized_semantic, semantic.output)
        report_progress(progress, "timing", detail="analyzing periodic state timing")
        state_timing = analyze_normalized_state_timing(optimized_semantic)
        report_progress(
            progress,
            "physical-lowering",
            detail="lowering Level/state semantics to abstract physical IR",
        )
        abstract_physical = _lower_level(
            optimized_semantic,
            enable_packing=optimize,
            state_timing=state_timing,
        )
        materialize_oracle_providers(optimized_semantic, abstract_physical, providers)

    return AbstractPhysicalLoweringResult(
        semantic_ir=semantic,
        optimized_ir=optimized_semantic,
        state_timing=state_timing,
        abstract_physical=abstract_physical,
        clocked=clocked,
    )


def compile_circuit(
    source: Circuit | CircuitModule,
    *,
    optimize: bool = True,
    blueprint_safe_wire_span: float = DEFAULT_SAFE_WIRE_SPAN,
    placement: PlacementOptions | None = None,
    physical_anchors: Mapping[str, Position] | None = None,
    progress: ProgressCallback | None = None,
    oracle_providers: Mapping[str, OracleProvider] | None = None,
) -> CompilationResult:
    """Compile semantic dataflow through physical synthesis to a final Layout and blueprint.

    Level modules retain the established optimizer/timing/lowering route. Event-bearing modules use
    clock-aware timing and the physical Event lowerer; semantic Event optimization and packing
    remain disabled until those transforms carry explicit clock proofs.

    Oracle providers are target-side bindings: they are absent from deterministic semantic
    evaluation and are inserted into the abstract physical graph before joint physical synthesis.
    ``physical_anchors`` resolves symbolic placement sites declared by providers. Abstract lowering
    may leave those sites unresolved, but final placement requires a coordinate for every anchored
    entity.

    ``progress`` receives coarse compiler phases plus bounded placement/routing updates.
    Callbacks are observational only and do not affect deterministic compilation.
    """

    lowered = lower_to_abstract_physical(
        source,
        optimize=optimize,
        progress=progress,
        oracle_providers=oracle_providers,
    )
    layout = _synthesize(
        lowered.abstract_physical,
        safe_wire_span=blueprint_safe_wire_span,
        placement=placement,
        physical_anchors=physical_anchors,
        progress=progress,
    )

    if lowered.clocked:
        # Clock-aware packing is deliberately postponed. Treat the implemented route as its own
        # structural baseline rather than pretending the Level naive lowerer is comparable.
        naive_physical = layout.circuit
    elif optimize:
        report_progress(
            progress,
            "baseline",
            detail="building unpacked comparison circuit for savings statistics",
        )
        naive_abstract = _lower_level(
            lowered.optimized_ir,
            enable_packing=False,
            state_timing=lowered.state_timing,
        )
        materialize_oracle_providers(
            lowered.optimized_ir,
            naive_abstract,
            validate_oracle_provider_bindings(lowered.optimized_ir, oracle_providers),
        )
        naive_physical = _synthesize(
            naive_abstract,
            safe_wire_span=blueprint_safe_wire_span,
            placement=placement,
            physical_anchors=physical_anchors,
            progress=progress,
        ).circuit
    else:
        naive_physical = layout.circuit

    report_progress(progress, "blueprint", detail="encoding final Factorio blueprint")
    blueprint_json = layout_to_blueprint_json(layout)
    blueprint_string = encode_layout_blueprint_string(layout)
    report_progress(progress, "done", completed=1, total=1, detail="compilation complete")
    return CompilationResult(
        semantic_ir=lowered.semantic_ir,
        optimized_ir=lowered.optimized_ir,
        state_timing=lowered.state_timing,
        abstract_physical=lowered.abstract_physical,
        layout=layout,
        naive_physical=naive_physical,
        blueprint_json=blueprint_json,
        blueprint_string=blueprint_string,
    )
