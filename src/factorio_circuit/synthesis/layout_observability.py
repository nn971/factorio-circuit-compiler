"""Observational routed-layout optimization without changing the production annealer."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from math import ceil, exp
from random import Random

from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis import joint_layout as exact
from factorio_circuit.synthesis import layout_optimizer as routed
from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.layout_optimizer import (
    LayoutOptimizationProblem,
    LayoutOptimizationResult,
)
from factorio_circuit.synthesis.placement import PlacementOptions

Objective = tuple[int, float, float]


@dataclass(frozen=True, slots=True)
class OptimizationStats:
    """Observational counters for one joint-annealing run.

    Rejection counters are mutually exclusive and, together with accepted moves, account for every
    proposal attempt. The history contains the best exact lexicographic physical objective before
    the first epoch and after every completed epoch.
    """

    proposals_attempted: int = 0
    accepted_moves: int = 0
    noop_rejections: int = 0
    geometry_rejections: int = 0
    wire_reach_rejections: int = 0
    metropolis_rejections: int = 0
    implementation_proposals: int = 0
    relay_proposals: int = 0
    implementation_moves_accepted: int = 0
    relay_moves_accepted: int = 0
    swap_attempts: int = 0
    swaps_accepted: int = 0
    relay_deletions: int = 0
    topology_rebuild_attempts: int = 0
    topology_rebuild_successes: int = 0
    epochs_completed: int = 0
    epochs_since_last_improvement: int = 0
    best_objective_history: tuple[Objective, ...] = ()

    @property
    def accounted_proposals(self) -> int:
        return (
            self.accepted_moves
            + self.noop_rejections
            + self.geometry_rejections
            + self.wire_reach_rejections
            + self.metropolis_rejections
        )


@dataclass(frozen=True, slots=True)
class ObservedLayoutOptimizationResult:
    """Normal fail-safe optimization result plus annealer-only observational statistics."""

    optimization: LayoutOptimizationResult
    stats: OptimizationStats


@dataclass(slots=True)
class _MutableOptimizationStats:
    proposals_attempted: int = 0
    accepted_moves: int = 0
    noop_rejections: int = 0
    geometry_rejections: int = 0
    wire_reach_rejections: int = 0
    metropolis_rejections: int = 0
    implementation_proposals: int = 0
    relay_proposals: int = 0
    implementation_moves_accepted: int = 0
    relay_moves_accepted: int = 0
    swap_attempts: int = 0
    swaps_accepted: int = 0
    relay_deletions: int = 0
    topology_rebuild_attempts: int = 0
    topology_rebuild_successes: int = 0
    epochs_completed: int = 0
    epochs_since_last_improvement: int = 0
    best_objective_history: list[Objective] = field(default_factory=list)

    def snapshot(self) -> OptimizationStats:
        return OptimizationStats(
            proposals_attempted=self.proposals_attempted,
            accepted_moves=self.accepted_moves,
            noop_rejections=self.noop_rejections,
            geometry_rejections=self.geometry_rejections,
            wire_reach_rejections=self.wire_reach_rejections,
            metropolis_rejections=self.metropolis_rejections,
            implementation_proposals=self.implementation_proposals,
            relay_proposals=self.relay_proposals,
            implementation_moves_accepted=self.implementation_moves_accepted,
            relay_moves_accepted=self.relay_moves_accepted,
            swap_attempts=self.swap_attempts,
            swaps_accepted=self.swaps_accepted,
            relay_deletions=self.relay_deletions,
            topology_rebuild_attempts=self.topology_rebuild_attempts,
            topology_rebuild_successes=self.topology_rebuild_successes,
            epochs_completed=self.epochs_completed,
            epochs_since_last_improvement=self.epochs_since_last_improvement,
            best_objective_history=tuple(self.best_objective_history),
        )


def _record_geometry_rejection(stats: _MutableOptimizationStats) -> None:
    stats.geometry_rejections += 1


def _complete_epoch(
    stats: _MutableOptimizationStats,
    *,
    best_score: Objective,
    improved: bool,
) -> None:
    stats.epochs_completed += 1
    if improved:
        stats.epochs_since_last_improvement = 0
    else:
        stats.epochs_since_last_improvement += 1
    stats.best_objective_history.append(best_score)


def _anneal_feasible_observed(
    state: exact._JointState,
    topology: incremental._FeasibleTopology,
    options: PlacementOptions,
    grid: base_placement._GridGeometry,
    stats: _MutableOptimizationStats,
    diagnostics: list[str] | None = None,
) -> incremental._FeasibleTopology:
    """Decision-equivalent copy of the production hot loop with observational counters only."""

    automatic_io = incremental._automatic_io_entity_ids(state.circuit, options)
    explicitly_anchored = set(options.anchors) | set(state.fixed_objects)
    movable_entities = [
        entity.id for entity in state.circuit.entities if entity.id not in explicitly_anchored
    ]
    initial_movable_count = len(movable_entities) + len(state.relay_positions)
    iterations = options.iterations
    if iterations is None:
        iterations = 0 if initial_movable_count < 6 else min(20_000, 30 * initial_movable_count)
    if iterations <= 0 or initial_movable_count == 0:
        return topology

    center = incremental._centroid([*state.positions.values(), *state.relay_positions.values()])
    unit_sites = set(grid.unit_slots)
    wide_sites = set(grid.slots)
    occupancy = incremental._SpatialOccupancy.build(state)
    rng = Random(options.random_seed ^ 0x61A7E5ED)
    initial_body_envelope = incremental._occupied_envelope(state)
    initial_left, initial_right, initial_top, initial_bottom = initial_body_envelope
    envelope_center = (
        (initial_left + initial_right) / 2.0,
        (initial_top + initial_bottom) / 2.0,
    )

    def target_envelope(scale: float) -> incremental.Envelope:
        half_width = (initial_right - initial_left) * scale / 2.0
        half_height = (initial_bottom - initial_top) * scale / 2.0
        return (
            envelope_center[0] - half_width,
            envelope_center[0] + half_width,
            envelope_center[1] - half_height,
            envelope_center[1] + half_height,
        )

    best_score = incremental._exact_score(state, topology, center)
    stats.best_objective_history.append(best_score)
    best_positions = dict(state.positions)
    best_relays = dict(state.relay_positions)
    best_relay_groups = dict(state.relay_groups)
    best_routing = topology.routing
    topology_rebuilds = {
        min(
            iterations,
            ceil(iterations * fraction / incremental._EPOCH_PROPOSALS)
            * incremental._EPOCH_PROPOSALS,
        )
        for fraction in incremental._ANNEAL_REBUILD_FRACTIONS
    }

    for epoch_start in range(0, iterations, incremental._EPOCH_PROPOSALS):
        epoch_end = min(iterations, epoch_start + incremental._EPOCH_PROPOSALS)
        movable_relays = sorted(set(state.relay_positions) - explicitly_anchored)
        movable_objects = sorted(set(movable_entities) | set(movable_relays))
        movable_set = set(movable_objects)
        if not movable_objects and not automatic_io:
            break
        shrink_progress = epoch_end / max(1, iterations)
        envelope_scale = 1.0 - (1.0 - incremental._FINAL_ENVELOPE_SCALE) * shrink_progress
        current_target_envelope = target_envelope(envelope_scale)

        outliers = [
            item
            for item in movable_objects
            if incremental._rectangle_overflow(
                state,
                item,
                state.object_position(item),
                current_target_envelope,
            )
            > incremental._EPSILON
        ]
        implementation_outliers = [item for item in outliers if item in state.positions]
        proposal_pool = (
            implementation_outliers
            or outliers
            or (movable_entities if movable_entities else movable_relays)
        )
        if not proposal_pool:
            _complete_epoch(stats, best_score=best_score, improved=False)
            continue

        for step in range(epoch_start, epoch_end):
            stats.proposals_attempted += 1
            progress = step / max(1, iterations - 1)
            normalized_temperature = 0.03**progress
            temperature = max(
                0.02,
                state.safe_span * (0.8 * normalized_temperature + 0.01),
            )
            object_id = proposal_pool[rng.randrange(len(proposal_pool))]
            selected_is_relay = object_id in state.relay_positions
            if selected_is_relay:
                stats.relay_proposals += 1
            else:
                stats.implementation_proposals += 1
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
                stats.noop_rejections += 1
                continue

            owners = occupancy.overlaps(object_id, target, ignored={object_id})
            other: int | None = None
            if owners:
                if len(owners) != 1:
                    _record_geometry_rejection(stats)
                    continue
                candidate = next(iter(owners))
                if state.object_position(candidate) != target:
                    _record_geometry_rejection(stats)
                    continue
                if candidate not in movable_set:
                    _record_geometry_rejection(stats)
                    continue
                if state.object_half_extent(candidate) != state.object_half_extent(object_id):
                    _record_geometry_rejection(stats)
                    continue
                if not incremental._position_is_legal(
                    state,
                    candidate,
                    current,
                    unit_sites,
                    wide_sites,
                ):
                    _record_geometry_rejection(stats)
                    continue
                other = candidate
                stats.swap_attempts += 1

            if not incremental._position_is_legal(
                state,
                object_id,
                target,
                unit_sites,
                wide_sites,
            ):
                _record_geometry_rejection(stats)
                continue
            ignored = {object_id} if other is None else {object_id, other}
            if occupancy.overlaps(object_id, target, ignored=ignored):
                _record_geometry_rejection(stats)
                continue
            if other is not None and occupancy.overlaps(other, current, ignored=ignored):
                _record_geometry_rejection(stats)
                continue

            targets = {object_id: target}
            if other is not None:
                targets[other] = current
            wire_delta = topology.proposal_delta(state, targets)
            if wire_delta is None:
                stats.wire_reach_rejections += 1
                continue

            compact_delta = sum(
                exact._compactness(position, center)
                - exact._compactness(state.object_position(item), center)
                for item, position in targets.items()
            )
            overflow_delta = sum(
                incremental._rectangle_overflow(state, item, position, current_target_envelope)
                - incremental._rectangle_overflow(
                    state,
                    item,
                    state.object_position(item),
                    current_target_envelope,
                )
                for item, position in targets.items()
            )
            delta = (
                wire_delta
                + compact_delta
                + incremental._ENVELOPE_OVERFLOW_WEIGHT * overflow_delta
            )
            if delta > 0 and rng.random() >= exp(-delta / temperature):
                stats.metropolis_rejections += 1
                continue

            occupancy.remove(object_id, current)
            if other is not None:
                occupancy.remove(other, target)
            exact._apply_move(state, object_id, target, other)
            occupancy.add(object_id, target)
            if other is not None:
                occupancy.add(other, current)
            topology.total_energy += wire_delta
            stats.accepted_moves += 1
            if selected_is_relay:
                stats.relay_moves_accepted += 1
            else:
                stats.implementation_moves_accepted += 1
            if other is not None:
                stats.swaps_accepted += 1

        relay_count_before = len(state.relay_positions)
        topology = incremental._simplify_feasible_topology(state, topology)
        stats.relay_deletions += relay_count_before - len(state.relay_positions)
        if epoch_end in topology_rebuilds and epoch_end < iterations:
            can_rebuild = not bool(state.fixed_objects & state.relay_positions.keys())
            if can_rebuild:
                stats.topology_rebuild_attempts += 1
            prior_topology = topology
            topology = incremental._try_rebuild_annealed_topology(
                state,
                topology,
                grid,
                diagnostics=diagnostics,
            )
            if can_rebuild and topology is not prior_topology:
                stats.topology_rebuild_successes += 1
        occupancy = incremental._SpatialOccupancy.build(state)
        score = incremental._exact_score(state, topology, center)
        improved = score < best_score
        if improved:
            best_score = score
            best_positions = dict(state.positions)
            best_relays = dict(state.relay_positions)
            best_relay_groups = dict(state.relay_groups)
            best_routing = topology.routing
        _complete_epoch(stats, best_score=best_score, improved=improved)

    state.positions.clear()
    state.positions.update(best_positions)
    state.relay_positions.clear()
    state.relay_positions.update(best_relays)
    state.relay_groups.clear()
    state.relay_groups.update(best_relay_groups)
    topology = incremental._FeasibleTopology.build(state, best_routing)
    return incremental._rebuild_automatic_interface_topology(state, topology, options, grid)


def _finish(
    result: LayoutOptimizationResult,
    stats: _MutableOptimizationStats,
) -> ObservedLayoutOptimizationResult:
    return ObservedLayoutOptimizationResult(result, stats.snapshot())


def optimize_physical_layout_observed(
    problem: LayoutOptimizationProblem,
    *,
    options: PlacementOptions,
) -> ObservedLayoutOptimizationResult:
    """Optimize with the production decisions while collecting an independent stats result."""

    options.validate()
    validated = routed._validated_embedding(problem)
    before = routed.physical_layout_metrics(problem.layout)
    movable_count = sum(
        entity.id not in problem.fixed_positions for entity in problem.layout.circuit.entities
    ) + sum(relay.entity_id not in problem.fixed_positions for relay in problem.layout.relays)
    proposal_budget = options.iterations
    if proposal_budget is None:
        proposal_budget = 0 if movable_count < 6 else min(20_000, 30 * movable_count)
    stats = _MutableOptimizationStats()
    if options.iterations == 0:
        return _finish(
            LayoutOptimizationResult(problem.layout, before, before, proposal_budget),
            stats,
        )

    state = validated.state
    topology = validated.topology
    original_state = state
    original_topology = topology
    best_layout = problem.layout
    best_metrics = before
    grid = routed._lattice_grid(problem.lattice)
    anneal_options = replace(
        options,
        anchors={},
        anchor_io=False,
        iterations=proposal_budget,
        restarts=1,
    )
    diagnostics: list[str] = []
    if proposal_budget >= incremental._EPOCH_PROPOSALS:
        state, topology, coarse_diagnostic = routed._try_coarse_compaction(state, topology, grid)
        if coarse_diagnostic is not None:
            diagnostics.append(coarse_diagnostic)
        elif state is not original_state:
            try:
                coarse_layout = routed._materialize_layout(problem.layout, state, topology.routing)
                routed._validated_embedding(replace(problem, layout=coarse_layout))
            except ValueError as exc:
                diagnostics.append(f"coarse compaction artifact rejected: {exc}")
                state = original_state
                topology = original_topology
            else:
                coarse_metrics = routed.physical_layout_metrics(coarse_layout)
                if coarse_metrics.objective < best_metrics.objective:
                    best_layout = coarse_layout
                    best_metrics = coarse_metrics

    try:
        optimized_topology = _anneal_feasible_observed(
            state,
            topology,
            anneal_options,
            grid,
            stats,
            diagnostics,
        )
        candidate = routed._materialize_layout(problem.layout, state, optimized_topology.routing)
        candidate_problem = replace(problem, layout=candidate)
        routed._validated_embedding(candidate_problem)
    except ValueError as exc:
        diagnostics.append(f"annealing candidate rejected: {exc}")
        return _finish(
            LayoutOptimizationResult(
                best_layout,
                before,
                best_metrics,
                proposal_budget,
                tuple(diagnostics),
            ),
            stats,
        )

    after = routed.physical_layout_metrics(candidate)
    if after.objective > best_metrics.objective:
        diagnostics.append("final candidate was valid but did not improve the physical objective")
        return _finish(
            LayoutOptimizationResult(
                best_layout,
                before,
                best_metrics,
                proposal_budget,
                tuple(diagnostics),
            ),
            stats,
        )
    return _finish(
        LayoutOptimizationResult(
            candidate,
            before,
            after,
            proposal_budget,
            tuple(diagnostics),
        ),
        stats,
    )


__all__ = [
    "ObservedLayoutOptimizationResult",
    "OptimizationStats",
    "optimize_physical_layout_observed",
]
