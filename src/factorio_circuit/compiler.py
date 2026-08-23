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
from factorio_circuit.ir.abstract_physical import (
    AbstractPhysicalCircuit,
    EntityPlacementConstraint,
    EntityPlacementMode,
    PhysicalAnchor,
)
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
from factorio_circuit.sampling import SamplingPolicy
from factorio_circuit.synthesis.layout import Layout
from factorio_circuit.synthesis.open_vector import synthesize_vector_layout
from factorio_circuit.synthesis.placement import PlacementOptions, Position

_TWO_WIRE_COLOR_ERROR = "abstract net constraints require more than the two Factorio wire colors"
_PORT_ANCHOR_PREFIX = "compiler-port:"


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
    sampling_policy: SamplingPolicy,
) -> AbstractPhysicalCircuit:
    return lower_normalized_vectors(
        module,
        enable_packing=enable_packing,
        state_timing=state_timing,
        sampling_policy=sampling_policy,
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


def _bind_port_positions(
    circuit: AbstractPhysicalCircuit,
    port_positions: Mapping[str, Position] | None,
    physical_anchors: Mapping[str, Position] | None,
) -> Mapping[str, Position] | None:
    """Pin named public I/O marker entities before placement/annealing.

    The existing ``physical_anchors`` mapping resolves symbolic deployment anchors carried by
    providers. ``port_positions`` is deliberately higher level: its keys are public compiler port
    names, and this helper turns them into ordinary abstract placement constraints before physical
    synthesis. Thus the placer sees the final module boundary instead of routing to it afterwards.
    """

    if not port_positions:
        return physical_anchors

    ports = [*circuit.inputs, *circuit.outputs]
    by_name: dict[str, list[object]] = {}
    for port in ports:
        by_name.setdefault(port.name, []).append(port)

    unknown = sorted(set(port_positions) - set(by_name))
    if unknown:
        raise ValueError(f"unknown compiler port position(s): {unknown!r}")
    ambiguous = sorted(name for name in port_positions if len(by_name[name]) != 1)
    if ambiguous:
        raise ValueError(f"ambiguous compiler port position(s): {ambiguous!r}")

    constrained = {constraint.entity for constraint in circuit.placement_constraints}
    resolved = dict(physical_anchors or {})
    for name, position in port_positions.items():
        port = by_name[name][0]
        endpoint = getattr(port, "endpoint")
        entity = endpoint.entity
        if entity in constrained:
            raise ValueError(f"compiler port {name!r} already has a placement constraint")
        symbolic_name = f"{_PORT_ANCHOR_PREFIX}{name}"
        existing = resolved.get(symbolic_name)
        if existing is not None and existing != position:
            raise ValueError(f"compiler port {name!r} conflicts with physical anchor binding")
        circuit.placement_constraints.append(
            EntityPlacementConstraint(
                entity,
                EntityPlacementMode.ANCHORED,
                PhysicalAnchor(symbolic_name),
            )
        )
        constrained.add(entity)
        resolved[symbolic_name] = position

    circuit.validate()
    return resolved


def lower_to_abstract_physical(
    source: Circuit | CircuitModule,
    *,
    optimize: bool = True,
    progress: ProgressCallback | None = None,
    oracle_providers: Mapping[str, OracleProvider] | None = None,
    sampling_policy: SamplingPolicy = SamplingPolicy.BEGINNING_OF_STEP,
) -> AbstractPhysicalLoweringResult:
    """Run the canonical compiler pipeline through abstract physical lowering only.

    This is the stable inspection boundary immediately before signal allocation, red/green
    assignment, physical-net coalescing, placement, and routing. It is useful for diagnostics and
    heavyweight benchmark census work that should not need to materialize a final layout.

    Semantic oracles require exact physical-provider coverage. Providers are materialized into the
    abstract physical graph before this function returns, so their entities and unresolved symbolic
    placement requirements participate in the same joint synthesis/layout pass as ordinary
    compiler-generated combinators.

    ``sampling_policy`` controls when phase-zero external Level inputs and oracles are physically
    observed inside one logical occurrence. The compatibility default snapshots them at the
    beginning; ``SamplingPolicy.ALAP`` may observe the live net later to avoid transport delays.
    """

    if not isinstance(sampling_policy, SamplingPolicy):
        raise TypeError("sampling_policy must be a SamplingPolicy")

    report_progress(progress, "frontend", detail="elaborating and lowering source program")
    source_output = _source_output(source)
    semantic = preserve_output_materializations(lower_frontend(source), source_output)
    providers = validate_oracle_provider_bindings(semantic, oracle_providers)
    clocked = contains_event_semantics(semantic)

    if clocked:
        if sampling_policy is not SamplingPolicy.BEGINNING_OF_STEP:
            raise ValueError("ALAP external sampling is currently supported for Level modules only")
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
            detail=(
                "lowering Level/state semantics to abstract physical IR "
                f"with sampling={sampling_policy.value}"
            ),
        )
        abstract_physical = _lower_level(
            optimized_semantic,
            enable_packing=optimize,
            state_timing=state_timing,
            sampling_policy=sampling_policy,
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
    port_positions: Mapping[str, Position] | None = None,
    progress: ProgressCallback | None = None,
    oracle_providers: Mapping[str, OracleProvider] | None = None,
    sampling_policy: SamplingPolicy = SamplingPolicy.BEGINNING_OF_STEP,
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

    ``port_positions`` pins named public input/output marker entities before placement. This is the
    preferred boundary primitive for constrained components: annealing therefore optimizes the
    implementation around its final docks rather than adding long post-layout routes to arbitrary
    anchor coordinates.

    ``sampling_policy`` is a target-side observation policy. ``BEGINNING_OF_STEP`` preserves the
    historical snapshot behavior. ``ALAP`` lets every phase-zero external Level input/oracle remain
    live until its physical consumer, eliminating identity-delay transport when no explicit logical
    reindexing requires an older sample.

    ``progress`` receives coarse compiler phases plus bounded placement/routing updates.
    Callbacks are observational only and do not affect deterministic compilation.
    """

    lowered = lower_to_abstract_physical(
        source,
        optimize=optimize,
        progress=progress,
        oracle_providers=oracle_providers,
        sampling_policy=sampling_policy,
    )
    actual_abstract = lowered.abstract_physical
    deployment_anchors = _bind_port_positions(
        actual_abstract,
        port_positions,
        physical_anchors,
    )
    packing_fallback = False
    try:
        layout = _synthesize(
            actual_abstract,
            safe_wire_span=blueprint_safe_wire_span,
            placement=placement,
            physical_anchors=deployment_anchors,
            progress=progress,
        )
    except ValueError as exc:
        if not (
            optimize
            and not lowered.clocked
            and str(exc) == _TWO_WIRE_COLOR_ERROR
        ):
            raise

        report_progress(
            progress,
            "synthesis",
            detail=(
                "packed Level graph is not realizable with two wire colors; "
                "retrying without combinator packing"
            ),
        )
        actual_abstract = _lower_level(
            lowered.optimized_ir,
            enable_packing=False,
            state_timing=lowered.state_timing,
            sampling_policy=sampling_policy,
        )
        materialize_oracle_providers(
            lowered.optimized_ir,
            actual_abstract,
            validate_oracle_provider_bindings(lowered.optimized_ir, oracle_providers),
        )
        deployment_anchors = _bind_port_positions(
            actual_abstract,
            port_positions,
            physical_anchors,
        )
        layout = _synthesize(
            actual_abstract,
            safe_wire_span=blueprint_safe_wire_span,
            placement=placement,
            physical_anchors=deployment_anchors,
            progress=progress,
        )
        packing_fallback = True

    if lowered.clocked or packing_fallback:
        # Clock-aware packing is deliberately postponed. A wire-color fallback is already the
        # unpacked implementation, so in both cases the realized circuit is its own baseline.
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
            sampling_policy=sampling_policy,
        )
        materialize_oracle_providers(
            lowered.optimized_ir,
            naive_abstract,
            validate_oracle_provider_bindings(lowered.optimized_ir, oracle_providers),
        )
        baseline_anchors = _bind_port_positions(
            naive_abstract,
            port_positions,
            physical_anchors,
        )
        naive_physical = _synthesize(
            naive_abstract,
            safe_wire_span=blueprint_safe_wire_span,
            # The unpacked circuit is a structural comparison baseline only. Reusing exact
            # module-interface anchors here can make an otherwise valid optimized module fail
            # solely because the larger unpacked comparison cannot satisfy production geometry.
            # Placement does not affect ``PhysicalCircuit.combinator_count``, which is the only
            # statistic consumed from this baseline.
            placement=None,
            physical_anchors=baseline_anchors,
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
        abstract_physical=actual_abstract,
        layout=layout,
        naive_physical=naive_physical,
        blueprint_json=blueprint_json,
        blueprint_string=blueprint_string,
    )
