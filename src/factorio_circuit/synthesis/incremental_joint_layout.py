"""Incremental joint annealing for implementation entities and wire relays.

Ordinary proposals use a cached coarse physical-net tree and update only edges incident to moved
objects. Coarse edges may temporarily exceed wire reach: over-reach is a soft energy penalty rather
than a hard prerequisite. At epoch boundaries the optimizer tries to repair the current geometry
with relay entities placed on vacant legal one-tile sites, then evaluates an exact reach-safe tree.

Joint relays are blank constant combinators and therefore use the same tile-footprint collision
semantics as implementation combinators. In particular, two entities whose nominal tile boxes only
touch at an edge are legal neighbors. This is intentionally different from the historical free-space
wire router, whose extra clearance margin is not part of the discrete joint placement model.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from heapq import heappop, heappush
from math import ceil, exp, floor, hypot
from random import Random

from factorio_circuit.blueprint import routing as wire_routing
from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import ConstantCombinator, PhysicalCircuit, WireColor
from factorio_circuit.synthesis import joint_layout as exact
from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.placement import PlacementOptions, Position

_EPOCH_PROPOSALS = 256
_EPSILON = 1e-9
_RELAY_HALF_EXTENT = (0.5, 0.5)
Bucket = tuple[int, int]


@dataclass(slots=True)
class _TopologyCache:
    """Local coarse topology used by the annealing hot loop."""

    edges_by_group: dict[int, tuple[tuple[int, int], ...]]
    incident_edges: dict[int, tuple[tuple[int, int, int], ...]]
    total_energy: float

    @classmethod
    def build(
        cls,
        state: exact._JointState,
        *,
        require_reach: bool,
    ) -> _TopologyCache:
        edges_by_group: dict[int, tuple[tuple[int, int], ...]] = {}
        incident: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
        total_energy = 0.0

        for group in sorted(state.endpoints_by_group):
            vertices = state.group_vertices(group)
            maximum_span = state.safe_span if require_reach else None
            tree = exact._prim_tree(vertices, state.vertex_position, maximum_span=maximum_span)
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
                total_energy += _edge_energy(
                    _distance(state.object_position(left), state.object_position(right)),
                    state.safe_span,
                )

        return cls(
            edges_by_group=edges_by_group,
            incident_edges={item: tuple(edges) for item, edges in incident.items()},
            total_energy=total_energy,
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
    ) -> float:
        """Return the soft local wiring-energy change for one move or swap."""

        affected: set[tuple[int, int, int]] = set()
        for object_id in targets:
            affected.update(self.incident_edges.get(object_id, ()))

        delta = 0.0
        for _group, left, right in affected:
            left_before = state.object_position(left)
            right_before = state.object_position(right)
            left_after = targets.get(left, left_before)
            right_after = targets.get(right, right_before)
            delta += _edge_energy(_distance(left_after, right_after), state.safe_span)
            delta -= _edge_energy(_distance(left_before, right_before), state.safe_span)
        return delta


@dataclass(slots=True)
class _SpatialOccupancy:
    """Spatial hash using the discrete placer's nominal tile-footprint semantics."""

    state: exact._JointState
    buckets: dict[Bucket, set[int]]

    @classmethod
    def build(cls, state: exact._JointState) -> _SpatialOccupancy:
        result = cls(state, defaultdict(set))
        for object_id in [*state.positions, *state.relay_positions]:
            result.add(object_id, state.object_position(object_id))
        return result

    @staticmethod
    def _box_keys(position: Position, half: tuple[float, float]) -> tuple[Bucket, ...]:
        # Buckets represent the interior of nominal tile footprints. Edge-touching boxes are legal,
        # matching placement._boxes_overlap(), so no neighboring-margin bucket expansion is needed.
        left = floor(position[0] - half[0] + _EPSILON)
        right = floor(position[0] + half[0] - _EPSILON)
        top = floor(position[1] - half[1] + _EPSILON)
        bottom = floor(position[1] + half[1] - _EPSILON)
        return tuple(
            (x, y)
            for x in range(left, right + 1)
            for y in range(top, bottom + 1)
        )

    def _keys(self, object_id: int, position: Position) -> tuple[Bucket, ...]:
        return self._box_keys(position, self.state.object_half_extent(object_id))

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
        return self._overlaps_box(
            position,
            self.state.object_half_extent(object_id),
            ignored=ignored,
        )

    def unit_site_is_free(self, position: Position) -> bool:
        return not self._overlaps_box(position, _RELAY_HALF_EXTENT, ignored=set())

    def _overlaps_box(
        self,
        position: Position,
        half: tuple[float, float],
        *,
        ignored: set[int],
    ) -> set[int]:
        candidates: set[int] = set()
        for key in self._box_keys(position, half):
            candidates.update(self.buckets.get(key, ()))
        candidates.difference_update(ignored)
        return {
            other_id
            for other_id in candidates
            if base_placement._boxes_overlap(
                position,
                half,
                self.state.object_position(other_id),
                self.state.object_half_extent(other_id),
            )
        }


def _edge_energy(distance: float, safe_span: float) -> float:
    """Cheap finite surrogate for relay demand plus routed length."""

    relay_estimate = max(0, ceil(distance / safe_span - 1e-12) - 1)
    overreach = max(0.0, distance - safe_span) / safe_span
    return 20.0 * relay_estimate + 6.0 * overreach**2 + 0.12 * distance / safe_span


def _prune_relays_to_terminal_paths(state: exact._JointState) -> None:
    """Drop relays unused by deterministic minimum-relay root-to-terminal paths."""

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


def _find_relay_chain(
    state: exact._JointState,
    group: int,
    left: int,
    right: int,
    free_sites: set[Position],
) -> tuple[Position, ...] | None:
    """Find a minimum-new-relay path through legal vacant unit sites.

    Existing terminals and same-net relays are zero-cost graph vertices, allowing different coarse
    edges to share already-created branch relays. Neighbor discovery is bucketed by wire reach.
    """

    start = state.object_position(left)
    goal = state.object_position(right)
    fixed_positions = {
        state.positions[endpoint.entity] for endpoint in state.endpoints_by_group[group]
    }
    fixed_positions.update(
        position
        for relay_id, position in state.relay_positions.items()
        if state.relay_groups[relay_id] == group
    )
    fixed_positions.update((start, goal))

    positions = sorted(fixed_positions)
    new_relay_cost = [0] * len(positions)
    position_to_index = {position: index for index, position in enumerate(positions)}
    for position in sorted(free_sites):
        if position in position_to_index:
            continue
        position_to_index[position] = len(positions)
        positions.append(position)
        new_relay_cost.append(1)

    start_index = position_to_index[start]
    goal_index = position_to_index[goal]
    cell = state.safe_span
    buckets: dict[Bucket, list[int]] = defaultdict(list)
    for index, position in enumerate(positions):
        buckets[(floor(position[0] / cell), floor(position[1] / cell))].append(index)

    infinity = (10**18, float("inf"))
    costs = [infinity for _ in positions]
    previous: list[int | None] = [None for _ in positions]
    costs[start_index] = (0, 0.0)
    queue: list[tuple[int, float, float, float, int]] = []
    heappush(queue, (0, 0.0, start[0], start[1], start_index))

    while queue:
        relay_cost, length, _x, _y, index = heappop(queue)
        if (relay_cost, length) != costs[index]:
            continue
        if index == goal_index:
            break
        position = positions[index]
        bucket_x = floor(position[0] / cell)
        bucket_y = floor(position[1] / cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for neighbor in buckets.get((bucket_x + dx, bucket_y + dy), ()):
                    if neighbor == index:
                        continue
                    edge_length = _distance(position, positions[neighbor])
                    if edge_length > state.safe_span + _EPSILON:
                        continue
                    candidate = (
                        relay_cost + new_relay_cost[neighbor],
                        length + edge_length,
                    )
                    if candidate < costs[neighbor]:
                        costs[neighbor] = candidate
                        previous[neighbor] = index
                        neighbor_position = positions[neighbor]
                        heappush(
                            queue,
                            (
                                candidate[0],
                                candidate[1],
                                neighbor_position[0],
                                neighbor_position[1],
                                neighbor,
                            ),
                        )

    if costs[goal_index] == infinity:
        return None

    path: list[int] = []
    cursor = goal_index
    while cursor != start_index:
        path.append(cursor)
        parent = previous[cursor]
        if parent is None:
            return None
        cursor = parent
    path.reverse()
    return tuple(
        positions[index]
        for index in path
        if index != goal_index and new_relay_cost[index] == 1
    )


def _repair_to_reach_safe(
    state: exact._JointState,
    grid: base_placement._GridGeometry,
    coarse: _TopologyCache,
) -> bool:
    """Try to realize every coarse tree edge with legal relay entities transactionally."""

    original_positions = dict(state.relay_positions)
    original_groups = dict(state.relay_groups)
    occupancy = _SpatialOccupancy.build(state)
    free_sites = {site for site in grid.unit_slots if occupancy.unit_site_is_free(site)}
    next_relay_id = max(
        [*(entity.id for entity in state.circuit.entities), *state.relay_positions],
        default=0,
    ) + 1

    # Repair the groups with the largest coarse over-reach first. That makes the sequential site
    # allocator less likely to spend a geometrically critical vacant site on an easy local net.
    group_order = sorted(
        state.endpoints_by_group,
        key=lambda group: (
            -sum(
                max(
                    0.0,
                    _distance(state.object_position(left), state.object_position(right))
                    - state.safe_span,
                )
                for left, right in coarse.edges_by_group[group]
            ),
            group,
        ),
    )

    for group in group_order:
        if exact._group_spanning_tree(state, group) is not None:
            continue
        for left, right in coarse.edges_by_group[group]:
            if _distance(state.object_position(left), state.object_position(right)) <= (
                state.safe_span + _EPSILON
            ):
                continue
            chain = _find_relay_chain(state, group, left, right, free_sites)
            if chain is None:
                state.relay_positions.clear()
                state.relay_positions.update(original_positions)
                state.relay_groups.clear()
                state.relay_groups.update(original_groups)
                return False
            for position in chain:
                relay_id = next_relay_id
                next_relay_id += 1
                state.relay_positions[relay_id] = position
                state.relay_groups[relay_id] = group
                occupancy.add(relay_id, position)
                free_sites.discard(position)

        if exact._group_spanning_tree(state, group) is None:
            state.relay_positions.clear()
            state.relay_positions.update(original_positions)
            state.relay_groups.clear()
            state.relay_groups.update(original_groups)
            return False

    try:
        _prune_relays_to_terminal_paths(state)
    except ValueError:
        state.relay_positions.clear()
        state.relay_positions.update(original_positions)
        state.relay_groups.clear()
        state.relay_groups.update(original_groups)
        return False
    return True


def _validate_joint_clearance(
    circuit: PhysicalCircuit,
    positions: dict[int, Position],
    routing: wire_routing.RoutingPlan,
) -> None:
    """Validate joint relays with the same nominal tile boxes used by placement."""

    originals = [
        (positions[entity.id], base_placement._entity_half_extent(entity), entity.id)
        for entity in circuit.entities
    ]
    relays = [(relay.position, _RELAY_HALF_EXTENT, relay.entity_id) for relay in routing.relays]
    for relay_pos, relay_half, relay_id in relays:
        for position, half, entity_id in originals:
            if base_placement._boxes_overlap(relay_pos, relay_half, position, half):
                raise ValueError(f"joint wire relay {relay_id} overlaps entity {entity_id}")
        for other_pos, other_half, other_id in relays:
            if other_id <= relay_id:
                continue
            if base_placement._boxes_overlap(relay_pos, relay_half, other_pos, other_half):
                raise ValueError(f"joint wire relay {relay_id} overlaps relay {other_id}")


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
    """Jointly anneal all movable implementation entities and layout-only relays."""

    endpoints_by_group, colors_by_group = exact._physical_groups(
        abstract_circuit,
        net_groups,
        net_colors,
    )
    grid = exact._matching_candidate_grid(circuit, positions, options)
    state = exact._JointState(
        circuit=circuit,
        endpoints_by_group=endpoints_by_group,
        colors_by_group=colors_by_group,
        positions=dict(positions),
        relay_positions={},
        relay_groups={},
        safe_span=safe_wire_span,
        forbidden_areas=(),
    )

    _anneal_incrementally(state, grid, options)

    try:
        _TopologyCache.build(state, require_reach=True)
    except ValueError:
        coarse = _TopologyCache.build(state, require_reach=False)
        if not _repair_to_reach_safe(state, grid, coarse):
            raise ValueError(
                "joint annealing could not repair the final placement into a reach-safe relay tree"
            ) from None

    _prune_relays_to_terminal_paths(state)
    routing = exact._routing_plan(state)
    all_positions = dict(state.positions)
    all_positions.update(state.relay_positions)
    wire_routing.validate_wire_spans(
        routing.wires,
        all_positions,
        maximum_span=safe_wire_span,
    )
    _validate_joint_clearance(circuit, state.positions, routing)
    return exact.JointLayoutResult(dict(state.positions), routing)


def _anneal_incrementally(
    state: exact._JointState,
    grid: base_placement._GridGeometry,
    options: PlacementOptions,
) -> None:
    movable_entities = exact._movable_entity_ids(state.circuit, options)
    initial_movable_count = len(movable_entities)
    iterations = options.iterations
    if iterations is None:
        iterations = 0 if initial_movable_count < 6 else min(20_000, 30 * initial_movable_count)
    if iterations <= 0 or initial_movable_count == 0:
        return

    unit_sites = set(grid.unit_slots)
    wide_sites = set(grid.slots)
    center = _centroid(list(state.positions.values()))
    rng = Random(options.random_seed ^ 0x61A7E5ED)

    topology = _TopologyCache.build(state, require_reach=False)
    occupancy = _SpatialOccupancy.build(state)
    best_score: tuple[int, float] | None = None
    best_positions: dict[int, Position] | None = None
    best_relays: dict[int, Position] | None = None
    best_relay_groups: dict[int, int] | None = None

    try:
        exact_topology = _TopologyCache.build(state, require_reach=True)
    except ValueError:
        pass
    else:
        movable_set = set(movable_entities)
        best_score = _exact_score(state, exact_topology, movable_set, center)
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
            topology.total_energy += wire_delta

        coarse = _TopologyCache.build(state, require_reach=False)
        if _repair_to_reach_safe(state, grid, coarse):
            topology = _TopologyCache.build(state, require_reach=True)
            occupancy = _SpatialOccupancy.build(state)
            movable_set = set(movable_entities) | set(state.relay_positions)
            score = _exact_score(state, topology, movable_set, center)
            if best_score is None or score < best_score:
                best_score = score
                best_positions = dict(state.positions)
                best_relays = dict(state.relay_positions)
                best_relay_groups = dict(state.relay_groups)
        else:
            topology = coarse
            occupancy = _SpatialOccupancy.build(state)

    if best_positions is not None and best_relays is not None and best_relay_groups is not None:
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
    return (len(state.relay_positions), topology.total_energy + compactness)


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


def _centroid(points: list[Position]) -> Position:
    if not points:
        return (0.0, 0.0)
    return (
        sum(x for x, _ in points) / len(points),
        sum(y for _, y in points) / len(points),
    )


def _distance(left: Position, right: Position) -> float:
    return hypot(left[0] - right[0], left[1] - right[1])
