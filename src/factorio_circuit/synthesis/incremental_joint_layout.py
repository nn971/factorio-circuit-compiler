"""Incremental joint annealing for implementation entities and wire relays.

The hot annealing loop deliberately does not rebuild a physical net's spanning tree. Each epoch
starts from an exact reach-safe tree and caches only its local edges. A proposal therefore touches
the cached edges incident to the moved object, while the exact O(k^2) tree calculation is paid only
at epoch boundaries and for final routing.

Relays and implementation combinators share one placement geometry. Implementation entities are
already seeded on the annealed grid; relay proposals use the same one-tile unit sites, so every
movable object stays out of the reserved walking/power corridors. The old relay-only forbidden-area
concept is used only as a compatibility input to the existing seed router; the areas supplied here
are the complete corridor geometry, not special relay locations.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from heapq import heappop, heappush
from math import exp, floor, hypot
from random import Random

from factorio_circuit.blueprint import routing as wire_routing
from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import ConstantCombinator, PhysicalCircuit, WireColor
from factorio_circuit.synthesis import joint_layout as exact
from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.placement import PlacementOptions, Position, RelayForbiddenArea

_EPOCH_PROPOSALS = 256
_EPSILON = 1e-9
Bucket = tuple[int, int]


@dataclass(slots=True)
class _TopologyCache:
    """Reach-safe local topology used by the annealing hot loop."""

    edges_by_group: dict[int, tuple[tuple[int, int], ...]]
    incident_edges: dict[int, tuple[tuple[int, int, int], ...]]
    total_length: float

    @classmethod
    def build(cls, state: exact._JointState) -> _TopologyCache:
        edges_by_group: dict[int, tuple[tuple[int, int], ...]] = {}
        incident: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
        total_length = 0.0

        for group in sorted(state.endpoints_by_group):
            tree = exact._group_spanning_tree(state, group)
            if tree is None:
                raise ValueError(f"physical net group {group} is outside conservative wire reach")
            object_edges: set[tuple[int, int]] = set()
            for left_vertex, right_vertex in tree[0]:
                left = left_vertex[1]
                right = right_vertex[1]
                if left == right:
                    continue
                edge = (left, right) if left < right else (right, left)
                object_edges.add(edge)
            ordered = tuple(sorted(object_edges))
            edges_by_group[group] = ordered
            for left, right in ordered:
                incident[left].append((group, left, right))
                incident[right].append((group, left, right))
                total_length += _distance(state.object_position(left), state.object_position(right))

        return cls(
            edges_by_group=edges_by_group,
            incident_edges={item: tuple(edges) for item, edges in incident.items()},
            total_length=total_length,
        )

    def preferred_position(
        self,
        state: exact._JointState,
        object_id: int,
        fallback: Position,
    ) -> Position:
        peers: set[int] = set()
        for _group, left, right in self.incident_edges.get(object_id, ()):
            peers.add(right if left == object_id else left)
        if not peers:
            return fallback
        return _centroid([state.object_position(peer) for peer in sorted(peers)])

    def proposal_delta(
        self,
        state: exact._JointState,
        targets: dict[int, Position],
    ) -> float | None:
        """Return local wire-length delta, or None when the cached tree would break reach."""

        affected: set[tuple[int, int, int]] = set()
        for object_id in targets:
            affected.update(self.incident_edges.get(object_id, ()))

        delta = 0.0
        for _group, left, right in affected:
            left_before = state.object_position(left)
            right_before = state.object_position(right)
            left_after = targets.get(left, left_before)
            right_after = targets.get(right, right_before)
            after = _distance(left_after, right_after)
            if after > state.safe_span + _EPSILON:
                return None
            delta += after - _distance(left_before, right_before)
        return delta


@dataclass(slots=True)
class _SpatialOccupancy:
    """Small spatial hash for collision tests during joint annealing."""

    state: exact._JointState
    buckets: dict[Bucket, set[int]]

    @classmethod
    def build(cls, state: exact._JointState) -> _SpatialOccupancy:
        result = cls(state, defaultdict(set))
        for object_id in [*state.positions, *state.relay_positions]:
            result.add(object_id, state.object_position(object_id))
        return result

    def _keys(self, object_id: int, position: Position) -> tuple[Bucket, ...]:
        half_x, half_y = self.state.object_half_extent(object_id)
        left = floor(position[0] - half_x + _EPSILON)
        right = floor(position[0] + half_x - _EPSILON)
        top = floor(position[1] - half_y + _EPSILON)
        bottom = floor(position[1] + half_y - _EPSILON)
        return tuple(
            (x, y)
            for x in range(left, right + 1)
            for y in range(top, bottom + 1)
        )

    def add(self, object_id: int, position: Position) -> None:
        for key in self._keys(object_id, position):
            self.buckets[key].add(object_id)

    def remove(self, object_id: int, position: Position) -> None:
        for key in self._keys(object_id, position):
            owners = self.buckets[key]
            owners.remove(object_id)
            if not owners:
                del self.buckets[key]

    def overlaps(
        self,
        object_id: int,
        position: Position,
        *,
        ignored: set[int],
    ) -> set[int]:
        candidates: set[int] = set()
        for key in self._keys(object_id, position):
            candidates.update(self.buckets.get(key, ()))
        candidates.difference_update(ignored)

        half = self.state.object_half_extent(object_id)
        return {
            other_id
            for other_id in candidates
            if wire_routing._boxes_overlap(
                position,
                half,
                self.state.object_position(other_id),
                self.state.object_half_extent(other_id),
            )
        }


def _prune_relays_to_terminal_paths(state: exact._JointState) -> None:
    """Drop relays unused by deterministic minimum-relay root-to-terminal paths.

    The historical exact pruning routine rebuilt a whole reach tree once for every candidate relay.
    Here each physical net pays one O(k^2) reach-graph construction plus one Dijkstra search. The
    union of the selected root-to-terminal paths is reach-connected by construction, so every relay
    outside that union can be removed in one batch.
    """

    keep_relays: set[int] = set()
    for group in sorted(state.endpoints_by_group):
        vertices = state.group_vertices(group)
        terminal_indexes = [index for index, vertex in enumerate(vertices) if vertex[0] == 0]
        if len(terminal_indexes) <= 1:
            continue

        adjacency: list[list[tuple[int, float]]] = [[] for _ in vertices]
        for left in range(len(vertices)):
            left_position = state.vertex_position(vertices[left])
            for right in range(left + 1, len(vertices)):
                distance = _distance(left_position, state.vertex_position(vertices[right]))
                if distance <= state.safe_span + _EPSILON:
                    adjacency[left].append((right, distance))
                    adjacency[right].append((left, distance))

        root = terminal_indexes[0]
        infinity = (10**18, float("inf"))
        costs = [infinity for _ in vertices]
        previous: list[int | None] = [None for _ in vertices]
        costs[root] = (0, 0.0)
        queue: list[tuple[int, float, tuple[int, int, int], int]] = []
        heappush(queue, (0, 0.0, vertices[root], root))

        while queue:
            relay_cost, length, _vertex_key, index = heappop(queue)
            if (relay_cost, length) != costs[index]:
                continue
            for neighbor, edge_length in adjacency[index]:
                enters_relay = 1 if vertices[neighbor][0] == 1 else 0
                candidate = (relay_cost + enters_relay, length + edge_length)
                if candidate < costs[neighbor]:
                    costs[neighbor] = candidate
                    previous[neighbor] = index
                    heappush(queue, (*candidate, vertices[neighbor], neighbor))

        for terminal in terminal_indexes[1:]:
            if costs[terminal] == infinity:
                raise ValueError(f"physical net group {group} is outside conservative wire reach")
            cursor = terminal
            while cursor != root:
                vertex = vertices[cursor]
                if vertex[0] == 1:
                    keep_relays.add(vertex[1])
                parent = previous[cursor]
                if parent is None:
                    raise AssertionError("reachable terminal path has no predecessor")
                cursor = parent

    for relay_id in tuple(state.relay_positions):
        if relay_id not in keep_relays:
            del state.relay_positions[relay_id]
            del state.relay_groups[relay_id]


def refine_incremental_joint_layout(
    circuit: PhysicalCircuit,
    abstract_circuit: abstract.AbstractPhysicalCircuit,
    net_groups: dict[int, int],
    net_colors: dict[int, WireColor],
    positions: dict[int, Position],
    *,
    safe_wire_span: float,
    options: PlacementOptions,
) -> exact.JointLayoutResult:
    """Jointly anneal all movable implementation entities and layout-only relays.

    Exact reach-safe topology is rebuilt only once per epoch. Within an epoch, connectivity is
    guaranteed by requiring every edge of the cached reach-safe tree to remain within wire reach.
    Relay pruning uses one terminal-connectivity graph search per net instead of rebuilding a whole
    exact tree once per candidate relay.
    """

    endpoints_by_group, colors_by_group = exact._physical_groups(
        abstract_circuit,
        net_groups,
        net_colors,
    )
    grid = exact._matching_candidate_grid(circuit, positions, options)
    reserved_areas = _corridor_areas(grid)
    state = exact._seed_state(
        circuit,
        endpoints_by_group,
        colors_by_group,
        positions,
        safe_wire_span=safe_wire_span,
        forbidden_areas=reserved_areas,
    )

    _prune_relays_to_terminal_paths(state)
    _anneal_incrementally(state, grid, options)
    _prune_relays_to_terminal_paths(state)

    routing = exact._routing_plan(state)
    all_positions = dict(state.positions)
    all_positions.update(state.relay_positions)
    wire_routing.validate_wire_spans(
        routing.wires,
        all_positions,
        maximum_span=safe_wire_span,
    )
    wire_routing.validate_entity_clearance(
        circuit,
        state.positions,
        routing,
        relay_forbidden_areas=reserved_areas,
    )
    return exact.JointLayoutResult(dict(state.positions), routing)


def _anneal_incrementally(
    state: exact._JointState,
    grid: base_placement._GridGeometry,
    options: PlacementOptions,
) -> None:
    movable_entities = exact._movable_entity_ids(state.circuit, options)
    initial_movable_count = len(movable_entities) + len(state.relay_positions)
    iterations = options.iterations
    if iterations is None:
        iterations = 0 if initial_movable_count < 6 else min(20_000, 30 * initial_movable_count)
    if iterations <= 0 or initial_movable_count == 0:
        return

    unit_sites = set(grid.unit_slots)
    wide_sites = set(grid.slots)
    center = _centroid([*state.positions.values(), *state.relay_positions.values()])
    rng = Random(options.random_seed ^ 0x61A7E5ED)

    topology = _TopologyCache.build(state)
    occupancy = _SpatialOccupancy.build(state)
    movable_set = set(movable_entities) | set(state.relay_positions)
    best_score = _exact_score(state, topology, movable_set, center)
    best_positions = dict(state.positions)
    best_relays = dict(state.relay_positions)
    best_relay_groups = dict(state.relay_groups)

    for epoch_start in range(0, iterations, _EPOCH_PROPOSALS):
        epoch_end = min(iterations, epoch_start + _EPOCH_PROPOSALS)
        movable_objects = sorted(set(movable_entities) | set(state.relay_positions))
        movable_set = set(movable_objects)
        if not movable_objects:
            break

        for step in range(epoch_start, epoch_end):
            progress = step / max(1, iterations - 1)
            normalized_temperature = 0.03**progress
            temperature = max(0.02, state.safe_span * (0.8 * normalized_temperature + 0.01))
            object_id = movable_objects[rng.randrange(len(movable_objects))]
            current = state.object_position(object_id)
            preferred = topology.preferred_position(state, object_id, center)
            target = _proposed_position(
                state,
                object_id,
                grid,
                preferred,
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
                other = candidate

            if not _position_is_legal(state, object_id, target, unit_sites, wide_sites):
                continue
            if other is not None and not _position_is_legal(
                state,
                other,
                current,
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

            compact_delta = sum(
                exact._compactness(position, center)
                - exact._compactness(state.object_position(item), center)
                for item, position in targets.items()
            )
            delta = wire_delta + compact_delta
            if delta > 0 and rng.random() >= exp(-delta / temperature):
                continue

            occupancy.remove(object_id, current)
            if other is not None:
                occupancy.remove(other, target)
            exact._apply_move(state, object_id, target, other)
            occupancy.add(object_id, target)
            if other is not None:
                occupancy.add(other, current)
            topology.total_length += wire_delta

        # Accurate work belongs here, not in the proposal loop. One reach graph prunes unused
        # relays, then one exact O(k^2) tree rebuild refreshes the local surrogate topology.
        _prune_relays_to_terminal_paths(state)
        topology = _TopologyCache.build(state)
        occupancy = _SpatialOccupancy.build(state)
        movable_set = set(movable_entities) | set(state.relay_positions)
        score = _exact_score(state, topology, movable_set, center)
        if score < best_score:
            best_score = score
            best_positions = dict(state.positions)
            best_relays = dict(state.relay_positions)
            best_relay_groups = dict(state.relay_groups)

    state.positions.clear()
    state.positions.update(best_positions)
    state.relay_positions.clear()
    state.relay_positions.update(best_relays)
    state.relay_groups.clear()
    state.relay_groups.update(best_relay_groups)


def _exact_score(
    state: exact._JointState,
    topology: _TopologyCache,
    movable_objects: set[int],
    center: Position,
) -> tuple[int, float]:
    compactness = sum(
        exact._compactness(state.object_position(object_id), center)
        for object_id in movable_objects
    )
    return (len(state.relay_positions), topology.total_length + compactness)


def _proposed_position(
    state: exact._JointState,
    object_id: int,
    grid: base_placement._GridGeometry,
    preferred: Position,
    rng: Random,
    normalized_temperature: float,
) -> Position:
    if object_id in state.relay_positions:
        if rng.random() < 0.12:
            return grid.unit_slots[rng.randrange(len(grid.unit_slots))]
        noise = state.safe_span * (1.2 * normalized_temperature + 0.05)
        target = (
            preferred[0] + rng.uniform(-noise, noise),
            preferred[1] + rng.uniform(-noise, noise),
        )
        x = min(grid.unit_x_positions, key=lambda value: (abs(value - target[0]), value))
        y = min(grid.y_positions, key=lambda value: (abs(value - target[1]), value))
        return (x, y)

    entity = state.circuit.entity_by_id(object_id)
    candidates = base_placement._candidate_positions(entity, grid)
    if rng.random() < 0.12:
        return candidates[rng.randrange(len(candidates))]
    noise = state.safe_span * (1.2 * normalized_temperature + 0.05)
    target = (
        preferred[0] + rng.uniform(-noise, noise),
        preferred[1] + rng.uniform(-noise, noise),
    )
    return base_placement._nearest_candidate(entity, target, grid)


def _position_is_legal(
    state: exact._JointState,
    object_id: int,
    position: Position,
    unit_sites: set[Position],
    wide_sites: set[Position],
) -> bool:
    if object_id in state.relay_positions:
        return position in unit_sites
    entity = state.circuit.entity_by_id(object_id)
    return position in (unit_sites if isinstance(entity, ConstantCombinator) else wide_sites)


def _corridor_areas(grid: base_placement._GridGeometry) -> tuple[RelayForbiddenArea, ...]:
    """Return the complete empty strips between placement blocks.

    The implementation grid already omits these strips. Supplying the same geometry to relay
    seeding makes corridors a property of the whole placement, rather than a relay-specific set of
    substation footprints.
    """

    left, right, top, bottom = grid.bounds
    result: list[RelayForbiddenArea] = []
    for previous, current in zip(grid.x_positions, grid.x_positions[1:], strict=False):
        corridor_left = previous + 1.0
        corridor_right = current - 1.0
        if corridor_right > corridor_left + _EPSILON:
            result.append((corridor_left, corridor_right, top, bottom))
    for previous, current in zip(grid.y_positions, grid.y_positions[1:], strict=False):
        corridor_top = previous + 0.5
        corridor_bottom = current - 0.5
        if corridor_bottom > corridor_top + _EPSILON:
            result.append((left, right, corridor_top, corridor_bottom))
    return tuple(result)


def _centroid(points: list[Position]) -> Position:
    if not points:
        return (0.0, 0.0)
    return (
        sum(x for x, _ in points) / len(points),
        sum(y for _, y in points) / len(points),
    )


def _distance(left: Position, right: Position) -> float:
    return hypot(left[0] - right[0], left[1] - right[1])
