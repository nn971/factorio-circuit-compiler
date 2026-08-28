"""Transactional translation and bounded optimization of rigid physical components.

D1 makes component geometry authoritative while freezing every component member. D2 adds one safe
kind of rigid motion: whole-tile translation with unchanged orientation. Every proposal moves all
members together, rebuilds the component-aware relay workspace, discards the old relay scaffold,
routes from scratch, and exact-validates the resulting serialized layout before it can be returned.

Quarter-turn changes remain deliberately unsupported here. ``Layout`` does not yet carry enough
orientation information to rotate arbitrary 2x1 combinators without a separate physical-orientation
contract.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from factorio_circuit.ir.physical import ConstantCombinator
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis import joint_layout as exact
from factorio_circuit.synthesis import layout_optimizer
from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.component_geometry import (
    ComponentLayoutOptimizationProblem,
    RigidComponentConstraint,
    lower_component_layout_problem,
    validate_component_layout_problem,
)
from factorio_circuit.synthesis.layout_optimizer import PhysicalLayoutMetrics, physical_layout_metrics
from factorio_circuit.synthesis.placement import Position

_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class RigidTranslationOptions:
    """Finite deterministic search budget for automatic rigid-component translation."""

    max_passes: int = 2
    max_candidates_per_component: int = 32
    footprint_margin_scale: float = 2.0

    def __post_init__(self) -> None:
        if self.max_passes < 0:
            raise ValueError("rigid translation max_passes must be non-negative")
        if self.max_candidates_per_component <= 0:
            raise ValueError("rigid translation max_candidates_per_component must be positive")
        if self.footprint_margin_scale <= 0.0:
            raise ValueError("rigid translation footprint_margin_scale must be positive")


@dataclass(frozen=True, slots=True)
class RigidComponentTranslationResult:
    """One exact rigid translation transaction, with original-problem fallback on failure."""

    problem: ComponentLayoutOptimizationProblem
    component_name: str
    source_origin: Position
    target_origin: Position
    succeeded: bool
    failure: str | None
    before: PhysicalLayoutMetrics
    after: PhysicalLayoutMetrics
    routing_unit_sites: int


@dataclass(frozen=True, slots=True)
class RigidComponentTranslationOptimizationResult:
    """Bounded coordinate-descent result over finite declared component origins."""

    problem: ComponentLayoutOptimizationProblem
    before: PhysicalLayoutMetrics
    after: PhysicalLayoutMetrics
    evaluated_candidates: int
    feasible_candidates: int
    accepted_moves: tuple[tuple[str, Position, Position], ...]
    passes_completed: int
    diagnostics: tuple[str, ...] = ()


def translate_rigid_component_transactionally(
    problem: ComponentLayoutOptimizationProblem,
    component_name: str,
    target_origin: Position,
    *,
    footprint_margin_scale: float = 2.0,
) -> RigidComponentTranslationResult:
    """Translate one component atomically and rebuild all relay routing from scratch.

    The current component-aware artifact must already be exact-valid. The target keeps the current
    quarter-turn orientation and must differ by an integral number of tiles in both axes, preserving
    the physical coordinate phase of every member. If ``allowed_origins`` is finite, the target must
    be one of those declared origins.
    """

    if footprint_margin_scale <= 0.0:
        raise ValueError("footprint_margin_scale must be positive")

    current_lowered = lower_component_layout_problem(problem)
    base = problem.layout_problem
    before = physical_layout_metrics(base.layout)
    component_index, component = _component_by_name(problem, component_name)
    source_origin = component.origin

    if target_origin == source_origin:
        return RigidComponentTranslationResult(
            problem,
            component_name,
            source_origin,
            target_origin,
            True,
            None,
            before,
            before,
            0,
        )

    translation_failure = _translation_legality_failure(component, target_origin)
    if translation_failure is not None:
        return _failed_translation(
            problem,
            component_name,
            source_origin,
            target_origin,
            before,
            translation_failure,
        )

    try:
        candidate_component = replace(component, origin=target_origin)
    except ValueError as exc:
        return _failed_translation(
            problem,
            component_name,
            source_origin,
            target_origin,
            before,
            str(exc),
        )

    candidate_components = list(problem.components)
    candidate_components[component_index] = candidate_component
    components = tuple(candidate_components)
    candidate_positions = _translated_implementation_positions(
        base.layout,
        candidate_component,
    )

    provisional_layout = replace(
        base.layout,
        positions=candidate_positions,
        relays=(),
        wires=(),
    )
    provisional_problem = ComponentLayoutOptimizationProblem(
        replace(base, layout=provisional_layout),
        components,
    )
    try:
        candidate_lowered = lower_component_layout_problem(
            provisional_problem,
            validate_base=False,
        )
        _validate_candidate_implementation_positions(candidate_lowered)
    except ValueError as exc:
        return _failed_translation(
            problem,
            component_name,
            source_origin,
            target_origin,
            before,
            str(exc),
        )

    embedding = layout_optimizer._validated_embedding(current_lowered)
    baseline_state = embedding.state
    candidate_state = exact._JointState(
        circuit=baseline_state.circuit,
        endpoints_by_group=baseline_state.endpoints_by_group,
        colors_by_group=baseline_state.colors_by_group,
        positions=dict(candidate_positions),
        relay_positions={},
        relay_groups={},
        safe_span=baseline_state.safe_span,
        forbidden_areas=tuple(base.lattice.forbidden_areas),
        fixed_objects=frozenset(candidate_lowered.fixed_positions),
    )

    grid = layout_optimizer._lattice_grid(candidate_lowered.lattice)
    routing_grid = layout_optimizer._bounded_routing_grid(
        grid,
        candidate_positions,
        base.safe_wire_span,
        footprint_margin_scale=footprint_margin_scale,
    )
    try:
        topology = incremental._construct_feasible_bootstrap(candidate_state, routing_grid)
        topology = _simplify_to_fixed_point(candidate_state, topology)
        candidate_layout = layout_optimizer._materialize_layout(
            base.layout,
            candidate_state,
            topology.routing,
        )
        candidate_problem = ComponentLayoutOptimizationProblem(
            replace(base, layout=candidate_layout),
            components,
        )
        validate_component_layout_problem(candidate_problem)
    except ValueError as exc:
        return _failed_translation(
            problem,
            component_name,
            source_origin,
            target_origin,
            before,
            str(exc),
            routing_unit_sites=len(routing_grid.unit_slots),
        )

    after = physical_layout_metrics(candidate_layout)
    return RigidComponentTranslationResult(
        candidate_problem,
        component_name,
        source_origin,
        target_origin,
        True,
        None,
        before,
        after,
        len(routing_grid.unit_slots),
    )


def optimize_rigid_component_translations(
    problem: ComponentLayoutOptimizationProblem,
    *,
    options: RigidTranslationOptions = RigidTranslationOptions(),
) -> RigidComponentTranslationOptimizationResult:
    """Greedily improve exact public layout objective using bounded rigid translations.

    Only components with finite ``allowed_origins`` participate automatically. Each pass evaluates
    at most ``max_candidates_per_component`` declared alternatives for each component. A move is
    accepted only when its exact ``(relay_count, occupied_area, wire_length)`` objective is strictly
    better than the current exact artifact. Strict improvement prevents cycles; ``max_passes`` gives
    an independent hard work bound.
    """

    validate_component_layout_problem(problem)
    current = problem
    before = physical_layout_metrics(problem.layout_problem.layout)
    current_metrics = before
    evaluated = 0
    feasible = 0
    accepted: list[tuple[str, Position, Position]] = []
    diagnostics: list[str] = []
    passes_completed = 0

    for pass_index in range(options.max_passes):
        passes_completed = pass_index + 1
        improved = False
        component_names = tuple(component.name for component in current.components)

        for component_name in component_names:
            _component_index, component = _component_by_name(current, component_name)
            if component.allowed_origins is None:
                diagnostics.append(
                    f"component {component_name!r} skipped: no finite allowed_origins search set"
                )
                continue

            candidates = tuple(
                origin for origin in component.allowed_origins if origin != component.origin
            )
            if len(candidates) > options.max_candidates_per_component:
                diagnostics.append(
                    f"component {component_name!r} candidate set truncated from "
                    f"{len(candidates)} to {options.max_candidates_per_component}"
                )
                candidates = candidates[: options.max_candidates_per_component]

            best: RigidComponentTranslationResult | None = None
            for target in candidates:
                evaluated += 1
                transaction = translate_rigid_component_transactionally(
                    current,
                    component_name,
                    target,
                    footprint_margin_scale=options.footprint_margin_scale,
                )
                if not transaction.succeeded:
                    diagnostics.append(
                        f"component {component_name!r} -> {target!r} rejected: "
                        f"{transaction.failure}"
                    )
                    continue
                feasible += 1
                if transaction.after.objective >= current_metrics.objective:
                    continue
                if best is None or transaction.after.objective < best.after.objective:
                    best = transaction

            if best is None:
                continue

            current = best.problem
            current_metrics = best.after
            accepted.append((component_name, best.source_origin, best.target_origin))
            improved = True

        if not improved:
            break

    validate_component_layout_problem(current)
    return RigidComponentTranslationOptimizationResult(
        current,
        before,
        current_metrics,
        evaluated,
        feasible,
        tuple(accepted),
        passes_completed,
        tuple(diagnostics),
    )


def _component_by_name(
    problem: ComponentLayoutOptimizationProblem,
    name: str,
) -> tuple[int, RigidComponentConstraint]:
    for index, component in enumerate(problem.components):
        if component.name == name:
            return index, component
    raise ValueError(f"unknown rigid component {name!r}")


def _translation_legality_failure(
    component: RigidComponentConstraint,
    target_origin: Position,
) -> str | None:
    if component.allowed_origins is not None and target_origin not in component.allowed_origins:
        return f"target origin {target_origin!r} is not declared for component {component.name!r}"
    dx = target_origin[0] - component.origin[0]
    dy = target_origin[1] - component.origin[1]
    if abs(dx - round(dx)) > _EPSILON or abs(dy - round(dy)) > _EPSILON:
        return "rigid component translation must be an integral number of tiles on both axes"
    return None


def _translated_implementation_positions(
    layout: object,
    component: RigidComponentConstraint,
) -> dict[int, Position]:
    physical_layout = layout
    circuit = physical_layout.circuit
    positions = {
        entity.id: physical_layout.positions[entity.id]
        for entity in circuit.entities
    }
    positions.update(component.member_positions())
    return positions


def _validate_candidate_implementation_positions(
    problem: layout_optimizer.LayoutOptimizationProblem,
) -> None:
    layout = problem.layout
    entities = {entity.id: entity for entity in layout.circuit.entities}
    implementation_ids = set(entities)
    if set(layout.positions) != implementation_ids:
        missing = sorted(implementation_ids - layout.positions.keys())
        unknown = sorted(layout.positions.keys() - implementation_ids)
        raise ValueError(
            f"candidate implementation positions have missing ids {missing} and unknown ids {unknown}"
        )

    fixed_ids = set(problem.fixed_positions)
    for object_id in fixed_ids:
        if layout.positions[object_id] != problem.fixed_positions[object_id]:
            raise ValueError(f"candidate moved fixed physical object {object_id}")

    unit_sites = set(problem.lattice.unit_sites)
    wide_sites = set(problem.lattice.wide_sites)
    for entity_id, entity in entities.items():
        if entity_id in fixed_ids:
            continue
        legal = unit_sites if isinstance(entity, ConstantCombinator) else wide_sites
        if layout.positions[entity_id] not in legal:
            raise ValueError(f"candidate implementation entity {entity_id} is not on a legal site")

    half_extents = {
        entity_id: base_placement._entity_half_extent(entity)
        for entity_id, entity in entities.items()
    }
    layout_optimizer._validate_object_clearance(
        layout.positions,
        half_extents,
        problem.lattice.forbidden_areas,
    )


def _simplify_to_fixed_point(
    state: exact._JointState,
    topology: incremental._FeasibleTopology,
) -> incremental._FeasibleTopology:
    while True:
        before = len(state.relay_positions)
        topology = incremental._simplify_feasible_topology(state, topology)
        if len(state.relay_positions) == before:
            return topology


def _failed_translation(
    problem: ComponentLayoutOptimizationProblem,
    component_name: str,
    source_origin: Position,
    target_origin: Position,
    before: PhysicalLayoutMetrics,
    failure: str,
    *,
    routing_unit_sites: int = 0,
) -> RigidComponentTranslationResult:
    return RigidComponentTranslationResult(
        problem,
        component_name,
        source_origin,
        target_origin,
        False,
        failure,
        before,
        before,
        routing_unit_sites,
    )


__all__ = [
    "RigidComponentTranslationOptimizationResult",
    "RigidComponentTranslationResult",
    "RigidTranslationOptions",
    "optimize_rigid_component_translations",
    "translate_rigid_component_transactionally",
]
