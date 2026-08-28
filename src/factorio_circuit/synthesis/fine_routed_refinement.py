"""Transactional area-first refinement of an already-valid routed physical layout.

C7 starts after a successful C5 routing checkpoint.  During the fine search the explicit routed
topology is held fixed: implementation entities and relay combinators may move, but every accepted
move must preserve the reach of every incident wire.  The best state is selected by exact occupied
bounding area first and exact wire length second.  Relay simplification is deliberately deferred
until after the best compact geometry has been restored, so an early removable relay cannot trump
all later area improvements merely because the public objective is relay-count-first.

After compaction, the routed topology is simplified to a fixed point, materialized, and exact-
validated.  The original validated layout remains the transactional fallback unless the final
artifact improves the public lexicographic physical objective.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import exp
from random import Random

from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis import joint_layout as exact
from factorio_circuit.synthesis import layout_optimizer
from factorio_circuit.synthesis.layout import Layout
from factorio_circuit.synthesis.layout_optimizer import (
    LayoutOptimizationProblem,
    PhysicalLayoutMetrics,
    physical_layout_metrics,
)


@dataclass(frozen=True, slots=True)
class FineRefinementOptions:
    """Bounded controls for one routed area-compaction transaction."""

    proposals: int = 4096
    random_seed: int = 0
    chunk_size: int = incremental._EPOCH_PROPOSALS
    final_envelope_scale: float = 0.70


@dataclass(frozen=True, slots=True)
class FineRefinementResult:
    """Validated improvement or the exact original routed fallback."""

    layout: Layout
    before: PhysicalLayoutMetrics
    after: PhysicalLayoutMetrics
    proposal_budget: int
    accepted: bool
    diagnostics: tuple[str, ...] = ()


def _area_score(
    tracker: incremental._ExactObjectiveTracker,
    state: exact._JointState,
) -> tuple[float, float]:
    _relay_count, area, wire_length = tracker.score(state)
    return (area, wire_length)


def _target_envelope(
    envelope: incremental.Envelope,
    scale: float,
) -> incremental.Envelope:
    left, right, top, bottom = envelope
    center_x = (left + right) / 2.0
    center_y = (top + bottom) / 2.0
    half_width = (right - left) * scale / 2.0
    half_height = (bottom - top) * scale / 2.0
    return (
        center_x - half_width,
        center_x + half_width,
        center_y - half_height,
        center_y + half_height,
    )


def _compact_with_fixed_topology(
    state: exact._JointState,
    topology: incremental._FeasibleTopology,
    grid,
    options: FineRefinementOptions,
) -> incremental._FeasibleTopology:
    """Area-first reach-safe annealing without epoch simplification or retopology."""

    explicitly_fixed = set(state.fixed_objects)
    movable_entities = [
        entity.id for entity in state.circuit.entities if entity.id not in explicitly_fixed
    ]
    if options.proposals <= 0:
        return topology

    unit_sites = set(grid.unit_slots)
    wide_sites = set(grid.slots)
    occupancy = incremental._SpatialOccupancy.build(state)
    rng = Random(options.random_seed ^ 0xC7A5EED)
    center = incremental._centroid([*state.positions.values(), *state.relay_positions.values()])
    initial_envelope = incremental._occupied_envelope(state)
    tracker = incremental._ExactObjectiveTracker.build(state, topology)
    best_score = _area_score(tracker, state)
    best_positions = dict(state.positions)
    best_relays = dict(state.relay_positions)
    best_relay_groups = dict(state.relay_groups)
    best_routing = topology.routing

    for epoch_start in range(0, options.proposals, options.chunk_size):
        epoch_end = min(options.proposals, epoch_start + options.chunk_size)
        progress = epoch_end / max(1, options.proposals)
        envelope_scale = 1.0 - (1.0 - options.final_envelope_scale) * progress
        target_envelope = _target_envelope(initial_envelope, envelope_scale)

        movable_relays = sorted(set(state.relay_positions) - explicitly_fixed)
        movable_objects = sorted(set(movable_entities) | set(movable_relays))
        movable_set = set(movable_objects)
        if not movable_objects:
            break
        outliers = [
            object_id
            for object_id in movable_objects
            if incremental._rectangle_overflow(
                state,
                object_id,
                state.object_position(object_id),
                target_envelope,
            )
            > incremental._EPSILON
        ]
        implementation_outliers = [item for item in outliers if item in state.positions]
        proposal_pool = implementation_outliers or outliers or movable_objects

        for step in range(epoch_start, epoch_end):
            global_progress = step / max(1, options.proposals - 1)
            normalized_temperature = 0.03**global_progress
            temperature = max(
                0.02,
                state.safe_span * (0.8 * normalized_temperature + 0.01),
            )
            object_id = proposal_pool[rng.randrange(len(proposal_pool))]
            current = state.object_position(object_id)
            preferred = topology.preferred_position(state, object_id, center)
            target = incremental._proposed_position(
                state,
                object_id,
                grid,
                preferred,
                current,
                rng,
                normalized_temperature,
            )
            if target == current:
                continue

            owners = occupancy.overlaps(object_id, target, ignored={object_id})
            other: int | None = None
            if owners:
                if len(owners) != 1:
                    continue
                candidate = next(iter(owners))
                if state.object_position(candidate) != target:
                    continue
                if candidate not in movable_set:
                    continue
                if state.object_half_extent(candidate) != state.object_half_extent(object_id):
                    continue
                if not incremental._position_is_legal(
                    state,
                    candidate,
                    current,
                    unit_sites,
                    wide_sites,
                ):
                    continue
                other = candidate

            if not incremental._position_is_legal(
                state,
                object_id,
                target,
                unit_sites,
                wide_sites,
            ):
                continue
            ignored = {object_id} if other is None else {object_id, other}
            if occupancy.overlaps(object_id, target, ignored=ignored):
                continue
            if other is not None and occupancy.overlaps(other, current, ignored=ignored):
                continue

            targets = {object_id: target}
            if other is not None:
                targets[other] = current
            wire_delta = topology.proposal_delta(state, targets)
            if wire_delta is None:
                continue
            exact_wire_delta = tracker.proposal_wire_length_delta(state, topology, targets)
            compact_delta = sum(
                exact._compactness(position, center)
                - exact._compactness(state.object_position(item), center)
                for item, position in targets.items()
            )
            overflow_delta = sum(
                incremental._rectangle_overflow(state, item, position, target_envelope)
                - incremental._rectangle_overflow(
                    state,
                    item,
                    state.object_position(item),
                    target_envelope,
                )
                for item, position in targets.items()
            )
            delta = (
                wire_delta
                + compact_delta
                + incremental._ENVELOPE_OVERFLOW_WEIGHT * overflow_delta
            )
            if delta > 0.0 and rng.random() >= exp(-delta / temperature):
                continue

            occupancy.remove(object_id, current)
            if other is not None:
                occupancy.remove(other, target)
            exact._apply_move(state, object_id, target, other)
            occupancy.add(object_id, target)
            if other is not None:
                occupancy.add(other, current)
            topology.total_energy += wire_delta
            tracker.accept_move(state, targets, exact_wire_delta)

            score = _area_score(tracker, state)
            if score < best_score:
                best_score = score
                best_positions = dict(state.positions)
                best_relays = dict(state.relay_positions)
                best_relay_groups = dict(state.relay_groups)
                best_routing = topology.routing

        occupancy = incremental._SpatialOccupancy.build(state)
        tracker = incremental._ExactObjectiveTracker.build(state, topology)

    state.positions.clear()
    state.positions.update(best_positions)
    state.relay_positions.clear()
    state.relay_positions.update(best_relays)
    state.relay_groups.clear()
    state.relay_groups.update(best_relay_groups)
    return incremental._FeasibleTopology.build(state, best_routing)


def _simplify_to_fixed_point(
    state: exact._JointState,
    topology: incremental._FeasibleTopology,
) -> incremental._FeasibleTopology:
    while True:
        before = len(state.relay_positions)
        topology = incremental._simplify_feasible_topology(state, topology)
        if len(state.relay_positions) == before:
            return topology


def refine_routed_layout_transactionally(
    problem: LayoutOptimizationProblem,
    *,
    options: FineRefinementOptions | None = None,
) -> FineRefinementResult:
    """Compact one valid routed layout, simplify relays, and exact-validate transactionally."""

    if options is None:
        options = FineRefinementOptions()
    if options.proposals < 0:
        raise ValueError("proposals must be non-negative")
    if options.chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if not 0.0 < options.final_envelope_scale <= 1.0:
        raise ValueError("final_envelope_scale must be in (0, 1]")

    validated = layout_optimizer._validated_embedding(problem)
    before = physical_layout_metrics(problem.layout)
    if options.proposals == 0:
        return FineRefinementResult(problem.layout, before, before, 0, False)

    state = validated.state
    topology = validated.topology
    grid = layout_optimizer._lattice_grid(problem.lattice)
    diagnostics: list[str] = []
    try:
        topology = _compact_with_fixed_topology(state, topology, grid, options)
        topology = _simplify_to_fixed_point(state, topology)
        candidate = layout_optimizer._materialize_layout(
            problem.layout,
            state,
            topology.routing,
        )
        layout_optimizer._validated_embedding(replace(problem, layout=candidate))
    except ValueError as exc:
        diagnostics.append(f"fine refinement candidate rejected: {exc}")
        return FineRefinementResult(
            problem.layout,
            before,
            before,
            options.proposals,
            False,
            tuple(diagnostics),
        )

    after = physical_layout_metrics(candidate)
    if after.objective >= before.objective:
        diagnostics.append("fine refinement produced no public-objective improvement")
        return FineRefinementResult(
            problem.layout,
            before,
            before,
            options.proposals,
            False,
            tuple(diagnostics),
        )

    return FineRefinementResult(
        candidate,
        before,
        after,
        options.proposals,
        True,
        tuple(diagnostics),
    )
