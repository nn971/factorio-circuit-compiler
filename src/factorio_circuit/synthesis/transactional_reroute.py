"""Fail-safe relay rebuilding for a proposed complete implementation placement.

The transaction deliberately ignores the routed relay scaffold in the input layout. It keeps the
logical physical circuit and connector-aware electrical groups, replaces implementation
coordinates with the caller's candidate, starts with zero relays, and asks the deterministic
incremental router to construct a fresh feasible topology. The rebuilt artifact is simplified and
exact-validated before it can become the transaction result.

A rejected proposal returns the original already-validated layout unchanged. Objective comparison is
left to the caller: this module answers only the C5 feasibility question.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from factorio_circuit.ir.physical import ConstantCombinator
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis import joint_layout as exact
from factorio_circuit.synthesis import layout_optimizer
from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.layout import Layout
from factorio_circuit.synthesis.layout_optimizer import (
    LayoutOptimizationProblem,
    PhysicalLayoutMetrics,
    physical_layout_metrics,
)
from factorio_circuit.synthesis.placement import Position


@dataclass(frozen=True, slots=True)
class TransactionalRerouteResult:
    """Validated fresh routing on success, or the exact validated fallback on failure."""

    layout: Layout
    succeeded: bool
    failure: str | None
    before: PhysicalLayoutMetrics
    after: PhysicalLayoutMetrics
    routing_unit_sites: int


def _validate_candidate_implementation_positions(
    problem: LayoutOptimizationProblem,
    positions: Mapping[int, Position],
) -> str | None:
    circuit = problem.layout.circuit
    entities = {entity.id: entity for entity in circuit.entities}
    implementation_ids = set(entities)
    relay_ids = {relay.entity_id for relay in problem.layout.relays}
    fixed_ids = set(problem.fixed_positions)

    fixed_relays = fixed_ids & relay_ids
    if fixed_relays:
        return f"fresh relay rebuilding cannot preserve fixed relay ids: {sorted(fixed_relays)}"
    if set(positions) != implementation_ids:
        missing = sorted(implementation_ids - positions.keys())
        unknown = sorted(positions.keys() - implementation_ids)
        return (
            f"candidate implementation positions have missing ids {missing} "
            f"and unknown ids {unknown}"
        )

    for entity_id in sorted(fixed_ids):
        if positions[entity_id] != problem.fixed_positions[entity_id]:
            return f"candidate moved fixed implementation entity {entity_id}"

    unit_sites = set(problem.lattice.unit_sites)
    wide_sites = set(problem.lattice.wide_sites)
    for entity_id, entity in entities.items():
        if entity_id in fixed_ids:
            continue
        legal = unit_sites if isinstance(entity, ConstantCombinator) else wide_sites
        if positions[entity_id] not in legal:
            return f"candidate implementation entity {entity_id} is not on a legal lattice site"

    half_extents = {
        entity_id: base_placement._entity_half_extent(entity)
        for entity_id, entity in entities.items()
    }
    try:
        layout_optimizer._validate_object_clearance(
            positions,
            half_extents,
            problem.lattice.forbidden_areas,
        )
    except ValueError as exc:
        return str(exc)
    return None


def _simplify_to_fixed_point(
    state: exact._JointState,
    topology: incremental._FeasibleTopology,
) -> incremental._FeasibleTopology:
    while True:
        before = len(state.relay_positions)
        topology = incremental._simplify_feasible_topology(state, topology)
        if len(state.relay_positions) == before:
            return topology


def reroute_implementation_transactionally(
    problem: LayoutOptimizationProblem,
    implementation_positions: Mapping[int, Position],
    *,
    footprint_margin_scale: float = 2.0,
) -> TransactionalRerouteResult:
    """Discard old relays, rebuild routing from scratch, and exact-validate transactionally."""

    if footprint_margin_scale <= 0.0:
        raise ValueError("footprint_margin_scale must be positive")

    layout_optimizer.validate_physical_layout(problem)
    before = physical_layout_metrics(problem.layout)
    failure = _validate_candidate_implementation_positions(problem, implementation_positions)
    if failure is not None:
        return TransactionalRerouteResult(problem.layout, False, failure, before, before, 0)

    embedding = layout_optimizer._validated_embedding(problem)
    baseline_state = embedding.state
    candidate_positions = dict(implementation_positions)
    candidate_state = exact._JointState(
        circuit=baseline_state.circuit,
        endpoints_by_group=baseline_state.endpoints_by_group,
        colors_by_group=baseline_state.colors_by_group,
        positions=candidate_positions,
        relay_positions={},
        relay_groups={},
        safe_span=baseline_state.safe_span,
        forbidden_areas=baseline_state.forbidden_areas,
        fixed_objects=baseline_state.fixed_objects,
    )

    grid = layout_optimizer._lattice_grid(problem.lattice)
    routing_grid = layout_optimizer._bounded_routing_grid(
        grid,
        candidate_positions,
        problem.safe_wire_span,
        footprint_margin_scale=footprint_margin_scale,
    )
    try:
        topology = incremental._construct_feasible_bootstrap(candidate_state, routing_grid)
        topology = _simplify_to_fixed_point(candidate_state, topology)
        candidate_layout = layout_optimizer._materialize_layout(
            problem.layout,
            candidate_state,
            topology.routing,
        )
        candidate_problem = replace(problem, layout=candidate_layout)
        layout_optimizer.validate_physical_layout(candidate_problem)
    except ValueError as exc:
        return TransactionalRerouteResult(
            problem.layout,
            False,
            str(exc),
            before,
            before,
            len(routing_grid.unit_slots),
        )

    after = physical_layout_metrics(candidate_layout)
    return TransactionalRerouteResult(
        candidate_layout,
        True,
        None,
        before,
        after,
        len(routing_grid.unit_slots),
    )
