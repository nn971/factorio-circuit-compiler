"""Observational routed-layout optimization without changing the production annealer."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from math import ceil, exp
from random import Random
from threading import RLock
from typing import Any

from factorio_circuit.blueprint import routing as wire_routing
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
_HELPER_OBSERVATION_LOCK = RLock()
_ACTIVE_WORK_STATS: ContextVar[_MutableOptimizationStats | None] = ContextVar(
    "factorio_layout_observability_work_stats",
    default=None,
)
_ROUTING_SEARCH_DEPTH: ContextVar[int] = ContextVar(
    "factorio_layout_observability_routing_depth",
    default=0,
)


@dataclass(frozen=True, slots=True)
class OptimizationStats:
    """Observational counters for one joint-annealing run.

    Rejection counters are mutually exclusive and, together with accepted moves, account for every
    proposal attempt. Relay simplification counters classify every deletion performed by observed
    simplifier calls. Routing queue pops count priority-queue work inside relay-path searches. The
    history contains the best exact lexicographic physical objective before the first epoch and
    after every completed epoch.
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
    relay_isolated_deletions: int = 0
    relay_leaf_deletions: int = 0
    relay_degree_two_bypasses: int = 0
    simplification_calls: int = 0
    topology_rebuild_attempts: int = 0
    topology_rebuild_successes: int = 0
    routing_search_calls: int = 0
    negotiated_routing_search_calls: int = 0
    routing_search_failures: int = 0
    routing_queue_pops: int = 0
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

    @property
    def classified_relay_deletions(self) -> int:
        return (
            self.relay_isolated_deletions
            + self.relay_leaf_deletions
            + self.relay_degree_two_bypasses
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
    relay_isolated_deletions: int = 0
    relay_leaf_deletions: int = 0
    relay_degree_two_bypasses: int = 0
    simplification_calls: int = 0
    topology_rebuild_attempts: int = 0
    topology_rebuild_successes: int = 0
    routing_search_calls: int = 0
    negotiated_routing_search_calls: int = 0
    routing_search_failures: int = 0
    routing_queue_pops: int = 0
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
            relay_isolated_deletions=self.relay_isolated_deletions,
            relay_leaf_deletions=self.relay_leaf_deletions,
            relay_degree_two_bypasses=self.relay_degree_two_bypasses,
            simplification_calls=self.simplification_calls,
            topology_rebuild_attempts=self.topology_rebuild_attempts,
            topology_rebuild_successes=self.topology_rebuild_successes,
            routing_search_calls=self.routing_search_calls,
            negotiated_routing_search_calls=self.negotiated_routing_search_calls,
            routing_search_failures=self.routing_search_failures,
            routing_queue_pops=self.routing_queue_pops,
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


def _simplify_feasible_topology_observed(
    state: exact._JointState,
    topology: incremental._FeasibleTopology,
    stats: _MutableOptimizationStats,
) -> incremental._FeasibleTopology:
    """Decision-equivalent relay simplifier that classifies each deletion cause."""

    stats.simplification_calls += 1
    wires: dict[incremental.WireKey, wire_routing.RoutedWire] = {
        incremental._wire_key(wire): wire for wire in topology.routing.wires
    }
    incident: dict[int, set[incremental.WireKey]] = {}
    for key, wire in wires.items():
        incident.setdefault(wire.source_entity, set()).add(key)
        incident.setdefault(wire.target_entity, set()).add(key)

    queue = sorted(state.relay_positions, reverse=True)
    queued = set(queue)

    def enqueue(object_id: int) -> None:
        if object_id in state.relay_positions and object_id not in queued:
            queue.append(object_id)
            queued.add(object_id)

    def remove_wire(key: incremental.WireKey) -> wire_routing.RoutedWire:
        wire = wires.pop(key)
        incident.setdefault(wire.source_entity, set()).discard(key)
        incident.setdefault(wire.target_entity, set()).discard(key)
        return wire

    def add_wire(wire: wire_routing.RoutedWire) -> None:
        key = incremental._wire_key(wire)
        if key in wires:
            return
        wires[key] = wire
        incident.setdefault(wire.source_entity, set()).add(key)
        incident.setdefault(wire.target_entity, set()).add(key)

    while queue:
        relay_id = queue.pop()
        queued.discard(relay_id)
        if relay_id not in state.relay_positions:
            continue
        if relay_id in state.fixed_objects:
            continue
        relay_edges = tuple(sorted(incident.get(relay_id, ()), key=incremental._wire_key_sort_key))
        if len(relay_edges) > 2:
            continue

        if len(relay_edges) == 0:
            del state.relay_positions[relay_id]
            del state.relay_groups[relay_id]
            stats.relay_deletions += 1
            stats.relay_isolated_deletions += 1
            continue

        if len(relay_edges) == 1:
            wire = remove_wire(relay_edges[0])
            remote, _connector = incremental._remote_endpoint(wire, relay_id)
            del state.relay_positions[relay_id]
            del state.relay_groups[relay_id]
            stats.relay_deletions += 1
            stats.relay_leaf_deletions += 1
            enqueue(remote)
            continue

        first = wires[relay_edges[0]]
        second = wires[relay_edges[1]]
        if first.color is not second.color:
            continue
        left_entity, left_connector = incremental._remote_endpoint(first, relay_id)
        right_entity, right_connector = incremental._remote_endpoint(second, relay_id)
        if (
            incremental._distance(
                state.object_position(left_entity),
                state.object_position(right_entity),
            )
            > state.safe_span + incremental._EPSILON
        ):
            continue

        remove_wire(relay_edges[0])
        remove_wire(relay_edges[1])
        if (left_entity, left_connector) != (right_entity, right_connector):
            add_wire(
                wire_routing.RoutedWire(
                    left_entity,
                    left_connector,
                    right_entity,
                    right_connector,
                    first.color,
                )
            )
        del state.relay_positions[relay_id]
        del state.relay_groups[relay_id]
        stats.relay_deletions += 1
        stats.relay_degree_two_bypasses += 1
        enqueue(left_entity)
        enqueue(right_entity)

    routing = wire_routing.RoutingPlan(
        relays=tuple(
            relay for relay in topology.routing.relays if relay.entity_id in state.relay_positions
        ),
        wires=tuple(wires[key] for key in sorted(wires, key=str)),
    )
    return incremental._FeasibleTopology.build(state, routing)


def _replace_incremental_helper(name: str, value: Any) -> None:
    setattr(incremental, name, value)


@contextmanager
def _observe_helper_work(stats: _MutableOptimizationStats) -> Iterator[None]:
    """Count helper work while preserving helper return values and call ordering."""

    with _HELPER_OBSERVATION_LOCK:
        original_find = incremental._find_relay_chain
        original_find_negotiated = incremental._find_negotiated_relay_chain
        original_heappop = vars(incremental)["heappop"]
        original_simplify = incremental._simplify_feasible_topology
        stats_token = _ACTIVE_WORK_STATS.set(stats)

        def counting_heappop(heap: list[Any]) -> Any:
            active = _ACTIVE_WORK_STATS.get()
            if active is not None and _ROUTING_SEARCH_DEPTH.get() > 0:
                active.routing_queue_pops += 1
            return original_heappop(heap)

        def counting_find(*args: Any, **kwargs: Any) -> Any:
            active = _ACTIVE_WORK_STATS.get()
            if active is None:
                return original_find(*args, **kwargs)
            active.routing_search_calls += 1
            depth_token = _ROUTING_SEARCH_DEPTH.set(_ROUTING_SEARCH_DEPTH.get() + 1)
            try:
                result = original_find(*args, **kwargs)
            finally:
                _ROUTING_SEARCH_DEPTH.reset(depth_token)
            if result is None:
                active.routing_search_failures += 1
            return result

        def counting_find_negotiated(*args: Any, **kwargs: Any) -> Any:
            active = _ACTIVE_WORK_STATS.get()
            if active is None:
                return original_find_negotiated(*args, **kwargs)
            active.routing_search_calls += 1
            active.negotiated_routing_search_calls += 1
            depth_token = _ROUTING_SEARCH_DEPTH.set(_ROUTING_SEARCH_DEPTH.get() + 1)
            try:
                result = original_find_negotiated(*args, **kwargs)
            finally:
                _ROUTING_SEARCH_DEPTH.reset(depth_token)
            if result is None:
                active.routing_search_failures += 1
            return result

        def counting_simplify(
            state: exact._JointState,
            topology: incremental._FeasibleTopology,
        ) -> incremental._FeasibleTopology:
            active = _ACTIVE_WORK_STATS.get()
            if active is None:
                return original_simplify(state, topology)
            return _simplify_feasible_topology_observed(state, topology, active)

        _replace_incremental_helper("heappop", counting_heappop)
        _replace_incremental_helper("_find_relay_chain", counting_find)
        _replace_incremental_helper("_find_negotiated_relay_chain", counting_find_negotiated)
        _replace_incremental_helper("_simplify_feasible_topology", counting_simplify)
        try:
            yield
        finally:
            _replace_incremental_helper("_simplify_feasible_topology", original_simplify)
            _replace_incremental_helper("_find_negotiated_relay_chain", original_find_negotiated)
            _replace_incremental_helper("_find_relay_chain", original_find)
            _replace_incremental_helper("heappop", original_heappop)
            _ACTIVE_WORK_STATS.reset(stats_token)


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
    exact_tracker = (
        incremental._ExactObjectiveTracker.build(state, topology)
        if incremental._TRACK_EXACT_ACCEPTED_MOVES
        else None
    )
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
        proposal_pool = incremental._reach_mobile_proposal_pool(
            state,
            topology,
            proposal_pool,
            grid,
        )
        if not proposal_pool and not incremental._FILTER_REACH_IMMOBILE_PROPOSALS:
            _complete_epoch(stats, best_score=best_score, improved=False)
            continue

        epoch_improved = False
        steps = range(epoch_start, epoch_end) if proposal_pool else ()
        for step in steps:
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
            exact_wire_delta = (
                exact_tracker.proposal_wire_length_delta(state, topology, targets)
                if exact_tracker is not None
                else 0.0
            )

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
            weighted_overflow_delta = incremental._ENVELOPE_OVERFLOW_WEIGHT * overflow_delta
            delta = wire_delta + compact_delta + weighted_overflow_delta
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

            if exact_tracker is not None:
                exact_tracker.accept_move(state, targets, exact_wire_delta)
                accepted_score = incremental._accepted_move_exact_score(
                    state,
                    topology,
                    center,
                    exact_tracker,
                )
                if accepted_score < best_score:
                    best_score = accepted_score
                    best_positions = dict(state.positions)
                    best_relays = dict(state.relay_positions)
                    best_relay_groups = dict(state.relay_groups)
                    best_routing = topology.routing
                    epoch_improved = True

        topology = incremental._simplify_feasible_topology(state, topology)
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
        if score < best_score:
            best_score = score
            best_positions = dict(state.positions)
            best_relays = dict(state.relay_positions)
            best_relay_groups = dict(state.relay_groups)
            best_routing = topology.routing
            epoch_improved = True
        if exact_tracker is not None:
            exact_tracker = incremental._ExactObjectiveTracker.build(state, topology)
        _complete_epoch(stats, best_score=best_score, improved=epoch_improved)

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


def _optimize_physical_layout_observed_inner(
    problem: LayoutOptimizationProblem,
    *,
    options: PlacementOptions,
    stats: _MutableOptimizationStats,
) -> ObservedLayoutOptimizationResult:
    validated = routed._validated_embedding(problem)
    before = routed.physical_layout_metrics(problem.layout)
    movable_count = sum(
        entity.id not in problem.fixed_positions for entity in problem.layout.circuit.entities
    ) + sum(relay.entity_id not in problem.fixed_positions for relay in problem.layout.relays)
    proposal_budget = options.iterations
    if proposal_budget is None:
        proposal_budget = 0 if movable_count < 6 else min(20_000, 30 * movable_count)
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


def optimize_physical_layout_observed(
    problem: LayoutOptimizationProblem,
    *,
    options: PlacementOptions,
) -> ObservedLayoutOptimizationResult:
    """Optimize with production-equivalent decisions while collecting observational stats."""

    options.validate()
    stats = _MutableOptimizationStats()
    if options.iterations == 0:
        return _optimize_physical_layout_observed_inner(problem, options=options, stats=stats)
    with _observe_helper_work(stats):
        return _optimize_physical_layout_observed_inner(problem, options=options, stats=stats)


__all__ = [
    "ObservedLayoutOptimizationResult",
    "OptimizationStats",
    "optimize_physical_layout_observed",
]
