"""Transactional area-first refinement of an already-valid routed physical layout.

C7 starts after a successful C5 routing checkpoint. During the fine search the explicit routed
topology is held fixed. Ordinary proposals first try a single reach-safe object move; if packing or
wire reach blocks that move, a bounded push-and-drag proposal may translate the colliding objects
and the topology neighbors whose wires would otherwise become over-span. This gives dense layouts a
general local cluster move without assuming rows, strips, application structure, or a target shape.

The best search state is selected by exact occupied bounding area first and exact wire length second.
Relay simplification is deliberately deferred until after that compact geometry has been restored,
so an early removable relay cannot lexicographically trump all later area improvements. The final
artifact is simplified to a fixed point, materialized, exact-validated, and accepted only if it
improves the public physical objective; otherwise the original validated layout is returned.
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
from factorio_circuit.synthesis.placement import Position


@dataclass(frozen=True, slots=True)
class FineRefinementOptions:
    """Bounded controls for one routed area-compaction transaction."""

    proposals: int = 4096
    random_seed: int = 0
    chunk_size: int = incremental._EPOCH_PROPOSALS
    final_envelope_scale: float = 0.70
    max_cluster_size: int = 12


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


def _translated_targets(
    state: exact._JointState,
    cluster: set[int],
    delta: Position,
) -> dict[int, Position]:
    dx, dy = delta
    return {
        object_id: (
            state.object_position(object_id)[0] + dx,
            state.object_position(object_id)[1] + dy,
        )
        for object_id in cluster
    }


def _overstretched_external_neighbors(
    state: exact._JointState,
    topology: incremental._FeasibleTopology,
    cluster: set[int],
    targets: dict[int, Position],
) -> set[int]:
    offenders: set[int] = set()
    seen_wires = set()
    for object_id in cluster:
        for wire in topology.incident_wires.get(object_id, ()):
            if wire in seen_wires:
                continue
            seen_wires.add(wire)
            source_in = wire.source_entity in cluster
            target_in = wire.target_entity in cluster
            if source_in == target_in:
                continue
            moved_id = wire.source_entity if source_in else wire.target_entity
            remote_id = wire.target_entity if source_in else wire.source_entity
            if (
                incremental._distance(
                    targets[moved_id],
                    state.object_position(remote_id),
                )
                > state.safe_span + incremental._EPSILON
            ):
                offenders.add(remote_id)
    return offenders


def _cluster_translation(
    state: exact._JointState,
    topology: incremental._FeasibleTopology,
    occupancy: incremental._SpatialOccupancy,
    object_id: int,
    target: Position,
    movable: set[int],
    unit_sites: set[Position],
    wide_sites: set[Position],
    max_cluster_size: int,
) -> tuple[dict[int, Position], float] | None:
    """Grow a bounded translated cluster until packing and wire reach are both legal."""

    current = state.object_position(object_id)
    delta = (target[0] - current[0], target[1] - current[1])
    if delta == (0.0, 0.0):
        return None
    cluster = {object_id}

    while len(cluster) <= max_cluster_size:
        targets = _translated_targets(state, cluster, delta)
        if any(
            not incremental._position_is_legal(
                state,
                member,
                position,
                unit_sites,
                wide_sites,
            )
            for member, position in targets.items()
        ):
            return None

        blockers: set[int] = set()
        for member, position in targets.items():
            blockers.update(occupancy.overlaps(member, position, ignored=cluster))
        if blockers:
            additions = blockers - cluster
            if not additions <= movable or len(cluster | additions) > max_cluster_size:
                return None
            cluster.update(additions)
            continue

        offenders = _overstretched_external_neighbors(state, topology, cluster, targets)
        additions = offenders - cluster
        if additions:
            if not additions <= movable or len(cluster | additions) > max_cluster_size:
                return None
            cluster.update(additions)
            continue

        wire_delta = topology.proposal_delta(state, targets)
        if wire_delta is None:
            return None
        return targets, wire_delta
    return None


def _apply_targets(
    state: exact._JointState,
    occupancy: incremental._SpatialOccupancy,
    targets: dict[int, Position],
) -> None:
    previous = {object_id: state.object_position(object_id) for object_id in targets}
    for object_id, position in previous.items():
        occupancy.remove(object_id, position)
    for object_id, position in targets.items():
        exact._set_object_position(state, object_id, position)
    for object_id, position in targets.items():
        occupancy.add(object_id, position)


def _compact_with_fixed_topology(
    state: exact._JointState,
    topology: incremental._FeasibleTopology,
    grid,
    options: FineRefinementOptions,
) -> tuple[incremental._FeasibleTopology, tuple[str, ...]]:
    """Area-first reach-safe annealing without epoch simplification or retopology."""

    explicitly_fixed = set(state.fixed_objects)
    movable_entities = [
        entity.id for entity in state.circuit.entities if entity.id not in explicitly_fixed
    ]
    if options.proposals <= 0:
        return topology, ()

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

    attempted = 0
    legal = 0
    accepted = 0
    cluster_attempts = 0
    cluster_legal = 0
    cluster_accepted = 0
    best_updates = 0

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
            attempted += 1
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

            proposal = _cluster_translation(
                state,
                topology,
                occupancy,
                object_id,
                target,
                movable_set,
                unit_sites,
                wide_sites,
                1,
            )
            used_cluster = False
            if proposal is None and options.max_cluster_size > 1:
                cluster_attempts += 1
                proposal = _cluster_translation(
                    state,
                    topology,
                    occupancy,
                    object_id,
                    target,
                    movable_set,
                    unit_sites,
                    wide_sites,
                    options.max_cluster_size,
                )
                used_cluster = proposal is not None
            if proposal is None:
                continue
            targets, wire_delta = proposal
            legal += 1
            if used_cluster:
                cluster_legal += 1

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

            _apply_targets(state, occupancy, targets)
            topology.total_energy += wire_delta
            tracker.accept_move(state, targets, exact_wire_delta)
            accepted += 1
            if used_cluster:
                cluster_accepted += 1

            score = _area_score(tracker, state)
            if score < best_score:
                best_score = score
                best_positions = dict(state.positions)
                best_relays = dict(state.relay_positions)
                best_relay_groups = dict(state.relay_groups)
                best_routing = topology.routing
                best_updates += 1

        occupancy = incremental._SpatialOccupancy.build(state)
        tracker = incremental._ExactObjectiveTracker.build(state, topology)

    state.positions.clear()
    state.positions.update(best_positions)
    state.relay_positions.clear()
    state.relay_positions.update(best_relays)
    state.relay_groups.clear()
    state.relay_groups.update(best_relay_groups)
    topology = incremental._FeasibleTopology.build(state, best_routing)
    diagnostics = (
        "fine compaction work: "
        f"attempted={attempted}, legal={legal}, accepted={accepted}, "
        f"cluster_attempts={cluster_attempts}, cluster_legal={cluster_legal}, "
        f"cluster_accepted={cluster_accepted}, best_updates={best_updates}"
    )
    return topology, (diagnostics,)


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
    if options.max_cluster_size <= 0:
        raise ValueError("max_cluster_size must be positive")

    validated = layout_optimizer._validated_embedding(problem)
    before = physical_layout_metrics(problem.layout)
    if options.proposals == 0:
        return FineRefinementResult(problem.layout, before, before, 0, False)

    state = validated.state
    topology = validated.topology
    grid = layout_optimizer._lattice_grid(problem.lattice)
    diagnostics: list[str] = []
    try:
        topology, work_diagnostics = _compact_with_fixed_topology(
            state,
            topology,
            grid,
            options,
        )
        diagnostics.extend(work_diagnostics)
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
