"""Reach-safe joint annealing for implementation entities and wire relays.

The annealer starts from an explicitly reach-safe routed topology. Ordinary proposals are allowed to
move implementation combinators and relay combinators, but a proposal is rejected immediately if it
would make any cached incident wire exceed the configured safe span. This keeps the hot loop local:
proposal work scales with the moved objects' topology degree, not with the size of their physical
nets.

The bootstrap and every annealing move use the same discrete placement grid. Reserved corridors are
therefore unavailable to implementation entities and relays alike. If the dense implementation seed
cannot expose a reachable relay site near every terminal, bootstrap falls back to a deliberately
porous implementation placement on that same grid: every other row and column cell is left open as
routing workspace. This creates a connected local relay lattice before topology construction, after
which annealing compacts the already-feasible joint state.

Epoch boundaries simplify the already-feasible topology locally: useless relay leaves disappear and
degree-two relays are bypassed whenever their neighbors are directly in reach. No routine annealing
step reconstructs an all-pairs reach graph merely to rediscover feasibility that the cached routing
already guarantees.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
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
_SLACK_START = 0.85
_BOOTSTRAP_EXPANSIONS = 4
_BOOTSTRAP_ORDER_ATTEMPTS = 3
Bucket = tuple[int, int]
WireKey = tuple[int, int, int, int, WireColor]


@dataclass(slots=True)
class _FeasibleTopology:
    """Explicit reach-safe wiring topology cached by the hot loop."""

    routing: wire_routing.RoutingPlan
    incident_wires: dict[int, tuple[wire_routing.RoutedWire, ...]]
    neighbors: dict[int, tuple[int, ...]]
    total_energy: float

    @classmethod
    def build(
        cls,
        state: exact._JointState,
        routing: wire_routing.RoutingPlan,
    ) -> _FeasibleTopology:
        routing = _synchronize_relay_snapshot(state, routing)
        incident: dict[int, list[wire_routing.RoutedWire]] = defaultdict(list)
        neighbor_sets: dict[int, set[int]] = defaultdict(set)
        total_energy = 0.0

        for wire in routing.wires:
            source = state.object_position(wire.source_entity)
            target = state.object_position(wire.target_entity)
            distance = _distance(source, target)
            if distance > state.safe_span + _EPSILON:
                raise ValueError(
                    "reach-safe topology contains an over-span wire: "
                    f"{wire.source_entity}->{wire.target_entity} = {distance:.3f} "
                    f"> {state.safe_span:.3f}"
                )
            incident[wire.source_entity].append(wire)
            incident[wire.target_entity].append(wire)
            if wire.source_entity != wire.target_entity:
                neighbor_sets[wire.source_entity].add(wire.target_entity)
                neighbor_sets[wire.target_entity].add(wire.source_entity)
            total_energy += _wire_energy(distance, state.safe_span)

        return cls(
            routing=routing,
            incident_wires={object_id: tuple(wires) for object_id, wires in incident.items()},
            neighbors={
                object_id: tuple(sorted(neighbors))
                for object_id, neighbors in neighbor_sets.items()
            },
            total_energy=total_energy,
        )

    def preferred_position(
        self,
        state: exact._JointState,
        object_id: int,
        fallback: Position,
    ) -> Position:
        peers = self.neighbors.get(object_id, ())
        if not peers:
            return fallback
        return _centroid([state.object_position(peer) for peer in peers])

    def proposal_delta(
        self,
        state: exact._JointState,
        targets: dict[int, Position],
    ) -> float | None:
        """Return local energy delta, or ``None`` when the proposal breaks wire reach."""

        affected: set[wire_routing.RoutedWire] = set()
        for object_id in targets:
            affected.update(self.incident_wires.get(object_id, ()))

        delta = 0.0
        for wire in affected:
            source_before = state.object_position(wire.source_entity)
            target_before = state.object_position(wire.target_entity)
            source_after = targets.get(wire.source_entity, source_before)
            target_after = targets.get(wire.target_entity, target_before)
            after_distance = _distance(source_after, target_after)
            if after_distance > state.safe_span + _EPSILON:
                return None
            delta += _wire_energy(after_distance, state.safe_span)
            delta -= _wire_energy(_distance(source_before, target_before), state.safe_span)
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
        left = floor(position[0] - half[0] + _EPSILON)
        right = floor(position[0] + half[0] - _EPSILON)
        top = floor(position[1] - half[1] + _EPSILON)
        bottom = floor(position[1] + half[1] - _EPSILON)
        return tuple((x, y) for x in range(left, right + 1) for y in range(top, bottom + 1))

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
        return _box_overlaps_occupancy(
            self,
            position,
            self.state.object_half_extent(object_id),
            ignored=ignored,
        )


@dataclass(slots=True)
class _RelayWorkspace:
    """Reusable reach-bucket index for one bootstrap placement attempt."""

    safe_span: float
    buckets: dict[Bucket, tuple[Position, ...]]

    @classmethod
    def build(cls, free_sites: set[Position], safe_span: float) -> _RelayWorkspace:
        buckets: dict[Bucket, list[Position]] = defaultdict(list)
        for position in sorted(free_sites):
            buckets[(floor(position[0] / safe_span), floor(position[1] / safe_span))].append(
                position
            )
        return cls(
            safe_span,
            {key: tuple(positions) for key, positions in buckets.items()},
        )

    def nearby_sites(self, position: Position) -> tuple[Position, ...]:
        bucket_x = floor(position[0] / self.safe_span)
        bucket_y = floor(position[1] / self.safe_span)
        return tuple(
            candidate
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            for candidate in self.buckets.get((bucket_x + dx, bucket_y + dy), ())
        )


def _wire_energy(distance: float, safe_span: float) -> float:
    """Prefer short wires and preserve slack well before the hard reach boundary."""

    normalized = distance / safe_span
    slack_pressure = max(0.0, normalized - _SLACK_START)
    return 0.12 * normalized + 4.0 * slack_pressure**2


def _synchronize_relay_snapshot(
    state: exact._JointState,
    routing: wire_routing.RoutingPlan,
) -> wire_routing.RoutingPlan:
    """Materialize relay coordinates from the mutable joint state without rebuilding net trees."""

    by_id = {relay.entity_id: relay for relay in routing.relays}
    relay_ids = set(state.relay_positions)
    if set(by_id) != relay_ids:
        raise ValueError("reach-safe routing relay set disagrees with joint placement state")
    relays = tuple(
        wire_routing.BlueprintRelay(
            relay_id,
            state.relay_positions[relay_id],
            by_id[relay_id].description,
        )
        for relay_id in sorted(relay_ids)
    )
    return wire_routing.RoutingPlan(relays=relays, wires=routing.wires)


def _new_joint_state(
    circuit: PhysicalCircuit,
    endpoints_by_group: dict[int, tuple[abstract.Endpoint, ...]],
    colors_by_group: dict[int, WireColor],
    positions: dict[int, Position],
    *,
    safe_wire_span: float,
) -> exact._JointState:
    return exact._JointState(
        circuit=circuit,
        endpoints_by_group=endpoints_by_group,
        colors_by_group=colors_by_group,
        positions=dict(positions),
        relay_positions={},
        relay_groups={},
        safe_span=safe_wire_span,
        forbidden_areas=(),
    )


def _bootstrap_group_orders(
    group_order: list[tuple[float, int, tuple[tuple[exact.Vertex, exact.Vertex], ...]]],
) -> tuple[tuple[tuple[float, int, tuple[tuple[exact.Vertex, exact.Vertex], ...]], ...], ...]:
    """Return a few deterministic global rip-up orders for contested relay workspaces."""

    hardest_first = tuple(sorted(group_order))
    candidates = (
        hardest_first,
        tuple(sorted(group_order, key=lambda item: item[1])),
        tuple(reversed(hardest_first)),
    )
    result: list[tuple[tuple[float, int, tuple[tuple[exact.Vertex, exact.Vertex], ...]], ...]] = []
    seen: set[tuple[int, ...]] = set()
    for candidate in candidates:
        key = tuple(item[1] for item in candidate)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
        if len(result) >= _BOOTSTRAP_ORDER_ATTEMPTS:
            break
    return tuple(result)


def _construct_feasible_bootstrap(
    state: exact._JointState,
    grid: base_placement._GridGeometry,
) -> _FeasibleTopology:
    """Construct one reach-safe topology on the common corridor-aware placement grid."""

    already_reach_safe = all(
        exact._group_spanning_tree(state, group) is not None for group in state.endpoints_by_group
    )
    if already_reach_safe:
        routing = exact._routing_plan(state)
        return _FeasibleTopology.build(state, routing)

    implementation_occupancy = _SpatialOccupancy.build(state)
    base_free_sites = {
        site
        for site in grid.unit_slots
        if not _box_overlaps_occupancy(
            implementation_occupancy,
            site,
            _RELAY_HALF_EXTENT,
            ignored=set(),
        )
    }
    workspace = _RelayWorkspace.build(base_free_sites, state.safe_span)
    first_relay_id = max((entity.id for entity in state.circuit.entities), default=0) + 1

    group_order: list[tuple[float, int, tuple[tuple[exact.Vertex, exact.Vertex], ...]]] = []
    for group in state.endpoints_by_group:
        terminals = tuple(exact._terminal_vertex(item) for item in state.endpoints_by_group[group])
        tree = exact._prim_tree(terminals, state.vertex_position, maximum_span=None)
        assert tree is not None
        overreach = sum(
            max(
                0.0,
                _distance(state.vertex_position(left), state.vertex_position(right))
                - state.safe_span,
            )
            for left, right in tree[0]
        )
        group_order.append((-overreach, group, tree[0]))

    last_failure = "unknown routing failure"
    for ordered_groups in _bootstrap_group_orders(group_order):
        state.relay_positions.clear()
        state.relay_groups.clear()
        occupancy = _SpatialOccupancy.build(state)
        free_sites = set(base_free_sites)
        next_relay_id = first_relay_id
        failed = False

        for _negative_overreach, group, edges in ordered_groups:
            if exact._group_spanning_tree(state, group) is not None:
                continue
            for left, right in edges:
                source = state.vertex_position(left)
                target = state.vertex_position(right)
                if _distance(source, target) <= state.safe_span + _EPSILON:
                    continue
                chain = _find_relay_chain(
                    state,
                    group,
                    left[1],
                    right[1],
                    free_sites,
                    workspace,
                )
                if chain is None:
                    source_neighbors = sum(
                        _distance(source, site) <= state.safe_span + _EPSILON for site in free_sites
                    )
                    target_neighbors = sum(
                        _distance(target, site) <= state.safe_span + _EPSILON for site in free_sites
                    )
                    last_failure = (
                        f"group={group}; edge={left}->{right}; source={source}; target={target}; "
                        f"free_sites={len(free_sites)}; "
                        f"source_free_neighbors={source_neighbors}; "
                        f"target_free_neighbors={target_neighbors}; "
                        f"claimed_relays={len(state.relay_positions)}"
                    )
                    failed = True
                    break
                for position in chain:
                    relay_id = next_relay_id
                    next_relay_id += 1
                    state.relay_positions[relay_id] = position
                    state.relay_groups[relay_id] = group
                    occupancy.add(relay_id, position)
                    free_sites.discard(position)
            if failed:
                break
            if exact._group_spanning_tree(state, group) is None:
                last_failure = f"physical net {group} remained disconnected after relay routing"
                failed = True
                break

        if failed:
            continue

        _prune_relays_to_terminal_paths(state)
        routing = exact._routing_plan(state)
        return _FeasibleTopology.build(state, routing)

    state.relay_positions.clear()
    state.relay_groups.clear()
    raise ValueError(
        "could not construct an initial reach-safe joint topology after deterministic global "
        f"rip-up/reorder attempts: {last_failure}"
    )


def _box_overlaps_occupancy(
    occupancy: _SpatialOccupancy,
    position: Position,
    half: tuple[float, float],
    *,
    ignored: set[int],
) -> set[int]:
    candidates: set[int] = set()
    for key in occupancy._box_keys(position, half):
        candidates.update(occupancy.buckets.get(key, ()))
    candidates.difference_update(ignored)
    return {
        other_id
        for other_id in candidates
        if base_placement._boxes_overlap(
            position,
            half,
            occupancy.state.object_position(other_id),
            occupancy.state.object_half_extent(other_id),
        )
    }


def _find_relay_chain(
    state: exact._JointState,
    group: int,
    left: int,
    right: int,
    free_sites: set[Position],
    workspace: _RelayWorkspace,
) -> tuple[Position, ...] | None:
    """Find a feasible low-relay path through the cached vacant-site workspace."""

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

    fixed_buckets: dict[Bucket, list[Position]] = defaultdict(list)
    for position in sorted(fixed_positions):
        fixed_buckets[
            (floor(position[0] / state.safe_span), floor(position[1] / state.safe_span))
        ].append(position)

    infinity = (10**18, float("inf"))
    costs: dict[Position, tuple[int, float]] = {start: (0, 0.0)}
    previous: dict[Position, Position] = {}
    queue: list[tuple[int, float, int, float, float, float, Position]] = []

    def push(position: Position, cost: tuple[int, float]) -> None:
        distance_to_goal = _distance(position, goal)
        relay_hint = max(
            0,
            ceil(distance_to_goal / state.safe_span - 1e-12) - 1,
        )
        heappush(
            queue,
            (
                cost[0] + relay_hint,
                cost[1] + distance_to_goal,
                cost[0],
                cost[1],
                position[0],
                position[1],
                position,
            ),
        )

    push(start, (0, 0.0))
    while queue:
        _estimated_relays, _estimated_length, relay_cost, length, _x, _y, position = heappop(queue)
        if costs.get(position, infinity) != (relay_cost, length):
            continue
        if position == goal:
            break

        bucket_x = floor(position[0] / state.safe_span)
        bucket_y = floor(position[1] / state.safe_span)
        neighbors: set[Position] = set()
        for candidate in workspace.nearby_sites(position):
            if candidate == position:
                continue
            if candidate not in free_sites and candidate not in fixed_positions:
                continue
            if _distance(position, candidate) <= state.safe_span + _EPSILON:
                neighbors.add(candidate)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for candidate in fixed_buckets.get((bucket_x + dx, bucket_y + dy), ()):
                    if candidate == position:
                        continue
                    if _distance(position, candidate) <= state.safe_span + _EPSILON:
                        neighbors.add(candidate)

        for neighbor in sorted(neighbors):
            edge_length = _distance(position, neighbor)
            candidate_cost = (
                relay_cost + (0 if neighbor in fixed_positions else 1),
                length + edge_length,
            )
            if candidate_cost >= costs.get(neighbor, infinity):
                continue
            costs[neighbor] = candidate_cost
            previous[neighbor] = position
            push(neighbor, candidate_cost)

    if goal not in costs:
        return None

    path: list[Position] = []
    cursor = goal
    while cursor != start:
        path.append(cursor)
        parent = previous.get(cursor)
        if parent is None:
            return None
        cursor = parent
    path.reverse()
    return tuple(position for position in path if position != goal and position in free_sites)


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
                raise AssertionError(
                    f"reach-preserving annealing disconnected physical net group {group}"
                )
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


def _wire_key(wire: wire_routing.RoutedWire) -> WireKey:
    left = (wire.source_entity, wire.source_connector_id)
    right = (wire.target_entity, wire.target_connector_id)
    if right < left:
        left, right = right, left
    return (left[0], left[1], right[0], right[1], wire.color)


def _remote_endpoint(
    wire: wire_routing.RoutedWire,
    relay_id: int,
) -> tuple[int, int]:
    if wire.source_entity == relay_id:
        return (wire.target_entity, wire.target_connector_id)
    if wire.target_entity == relay_id:
        return (wire.source_entity, wire.source_connector_id)
    raise AssertionError(f"wire is not incident to relay {relay_id}")


def _simplify_feasible_topology(
    state: exact._JointState,
    topology: _FeasibleTopology,
) -> _FeasibleTopology:
    """Remove locally redundant relays without ever leaving the feasible region."""

    wires: dict[WireKey, wire_routing.RoutedWire] = {
        _wire_key(wire): wire for wire in topology.routing.wires
    }
    incident: dict[int, set[WireKey]] = defaultdict(set)
    for key, wire in wires.items():
        incident[wire.source_entity].add(key)
        incident[wire.target_entity].add(key)

    queue = sorted(state.relay_positions, reverse=True)
    queued = set(queue)

    def enqueue(object_id: int) -> None:
        if object_id in state.relay_positions and object_id not in queued:
            queue.append(object_id)
            queued.add(object_id)

    def remove_wire(key: WireKey) -> wire_routing.RoutedWire:
        wire = wires.pop(key)
        incident[wire.source_entity].discard(key)
        incident[wire.target_entity].discard(key)
        return wire

    def add_wire(wire: wire_routing.RoutedWire) -> None:
        key = _wire_key(wire)
        if key in wires:
            return
        wires[key] = wire
        incident[wire.source_entity].add(key)
        incident[wire.target_entity].add(key)

    while queue:
        relay_id = queue.pop()
        queued.discard(relay_id)
        if relay_id not in state.relay_positions:
            continue
        relay_edges = tuple(incident.get(relay_id, ()))
        if len(relay_edges) > 2:
            continue

        if len(relay_edges) == 0:
            del state.relay_positions[relay_id]
            del state.relay_groups[relay_id]
            continue

        if len(relay_edges) == 1:
            wire = remove_wire(relay_edges[0])
            remote, _connector = _remote_endpoint(wire, relay_id)
            del state.relay_positions[relay_id]
            del state.relay_groups[relay_id]
            enqueue(remote)
            continue

        first = wires[relay_edges[0]]
        second = wires[relay_edges[1]]
        if first.color is not second.color:
            continue
        left_entity, left_connector = _remote_endpoint(first, relay_id)
        right_entity, right_connector = _remote_endpoint(second, relay_id)
        if (
            _distance(
                state.object_position(left_entity),
                state.object_position(right_entity),
            )
            > state.safe_span + _EPSILON
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
        enqueue(left_entity)
        enqueue(right_entity)

    routing = wire_routing.RoutingPlan(
        relays=tuple(
            relay for relay in topology.routing.relays if relay.entity_id in state.relay_positions
        ),
        wires=tuple(wires[key] for key in sorted(wires, key=str)),
    )
    return _FeasibleTopology.build(state, routing)


def _validate_joint_clearance(
    state: exact._JointState,
    routing: wire_routing.RoutingPlan,
) -> None:
    """Validate relay clearance with the same spatial hash used by annealing."""

    routed_positions = {relay.entity_id: relay.position for relay in routing.relays}
    if set(routed_positions) != set(state.relay_positions):
        raise ValueError("reach-safe routing relay set disagrees with joint placement state")
    if routed_positions != state.relay_positions:
        raise ValueError("reach-safe routing relay positions disagree with joint placement state")
    occupancy = _SpatialOccupancy.build(state)
    for relay_id, position in state.relay_positions.items():
        overlaps = occupancy.overlaps(relay_id, position, ignored={relay_id})
        if overlaps:
            other_id = min(overlaps)
            if other_id in state.relay_positions:
                raise ValueError(f"joint wire relay {relay_id} overlaps relay {other_id}")
            raise ValueError(f"joint wire relay {relay_id} overlaps entity {other_id}")


def _expanded_bootstrap_grid(
    circuit: PhysicalCircuit,
    grid: base_placement._GridGeometry,
    options: PlacementOptions,
) -> base_placement._GridGeometry:
    """Double legal joint workspace while keeping all existing grid sites unchanged."""

    minimum_rows = max(len(circuit.inputs), len(circuit.outputs), 1) if options.anchor_io else 1
    target_slots = max(len(grid.slots) + 1, len(grid.slots) * 2)
    body_count = max(1, ceil(target_slots * options.target_fill))
    expanded = base_placement._candidate_grid(body_count, minimum_rows, options)
    while len(expanded.slots) <= len(grid.slots):
        body_count += max(1, ceil(len(grid.slots) * options.target_fill))
        expanded = base_placement._candidate_grid(body_count, minimum_rows, options)
    return expanded


def _porous_bootstrap_positions(
    circuit: PhysicalCircuit,
    preferred_positions: dict[int, Position],
    options: PlacementOptions,
    grid: base_placement._GridGeometry,
) -> dict[int, Position] | None:
    """Place implementation entities on alternating cells, leaving connected relay lanes."""

    entities = {entity.id: entity for entity in circuit.entities}
    movable = set(exact._movable_entity_ids(circuit, options))
    automatic_io = (
        base_placement._automatic_io_anchors(circuit, grid.bounds) if options.anchor_io else {}
    )
    effective_anchors = {**automatic_io, **options.anchors}
    base_placement._validate_anchors(circuit, effective_anchors)
    fixed = {
        entity_id: effective_anchors.get(entity_id, preferred_positions[entity_id])
        for entity_id in entities
        if entity_id not in movable
    }
    occupancy = base_placement._DiscreteOccupancy(entities, fixed)

    sparse_x = set(grid.x_positions[::2])
    sparse_y = set(grid.y_positions[::2])
    sparse_unit_x = {value for center in sparse_x for value in (center - 0.5, center + 0.5)}
    wide_candidates = tuple(
        position for position in grid.slots if position[0] in sparse_x and position[1] in sparse_y
    )
    unit_candidates = tuple(
        position
        for position in grid.unit_slots
        if position[0] in sparse_unit_x and position[1] in sparse_y
    )

    result = dict(fixed)
    order = sorted(
        movable,
        key=lambda entity_id: (
            0 if not isinstance(entities[entity_id], ConstantCombinator) else 1,
            entity_id,
        ),
    )
    for entity_id in order:
        entity = entities[entity_id]
        candidates = unit_candidates if isinstance(entity, ConstantCombinator) else wide_candidates
        preferred = preferred_positions[entity_id]
        legal = (candidate for candidate in candidates if occupancy.is_clear(entity_id, candidate))
        try:
            position = min(
                legal,
                key=lambda item: (
                    (item[0] - preferred[0]) ** 2 + (item[1] - preferred[1]) ** 2,
                    item,
                ),
            )
        except ValueError:
            return None
        result[entity_id] = position
        occupancy.add(entity_id, position)
    return result


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
    """Compact a reach-safe joint layout while preserving wire reach on every accepted move."""

    endpoints_by_group, colors_by_group = exact._physical_groups(
        abstract_circuit,
        net_groups,
        net_colors,
    )
    bootstrap_options = replace(options, iterations=0, restarts=1)
    preferred_positions = dict(positions)
    grid = exact._matching_candidate_grid(circuit, preferred_positions, bootstrap_options)
    state: exact._JointState | None = None
    topology: _FeasibleTopology | None = None
    last_error: ValueError | None = None

    dense_state = _new_joint_state(
        circuit,
        endpoints_by_group,
        colors_by_group,
        preferred_positions,
        safe_wire_span=safe_wire_span,
    )
    try:
        topology = _construct_feasible_bootstrap(dense_state, grid)
        state = dense_state
    except ValueError as exc:
        last_error = exc

    if state is None:
        for expansion in range(_BOOTSTRAP_EXPANSIONS + 1):
            porous_positions = _porous_bootstrap_positions(
                circuit,
                preferred_positions,
                bootstrap_options,
                grid,
            )
            if porous_positions is not None:
                candidate_state = _new_joint_state(
                    circuit,
                    endpoints_by_group,
                    colors_by_group,
                    porous_positions,
                    safe_wire_span=safe_wire_span,
                )
                try:
                    candidate_topology = _construct_feasible_bootstrap(candidate_state, grid)
                except ValueError as exc:
                    last_error = exc
                else:
                    state = candidate_state
                    topology = candidate_topology
                    break
            if expansion >= _BOOTSTRAP_EXPANSIONS:
                break
            grid = _expanded_bootstrap_grid(circuit, grid, bootstrap_options)

    if state is None or topology is None:
        detail = str(last_error) if last_error is not None else "unknown bootstrap failure"
        raise ValueError(
            "joint bootstrap is outside conservative wire reach or exhausted relay-site capacity; "
            "could not construct a reach-safe joint bootstrap from either the dense or porous "
            f"corridor-aware placement: {detail}"
        ) from last_error

    topology = _anneal_feasible(state, topology, options, grid)
    routing = _synchronize_relay_snapshot(state, topology.routing)
    all_positions = wire_routing.routed_positions(circuit, state.positions, routing)
    wire_routing.validate_wire_spans(
        routing.wires,
        all_positions,
        maximum_span=safe_wire_span,
    )
    _validate_joint_clearance(state, routing)
    return exact.JointLayoutResult(dict(state.positions), routing)


def _anneal_feasible(
    state: exact._JointState,
    topology: _FeasibleTopology,
    options: PlacementOptions,
    grid: base_placement._GridGeometry,
) -> _FeasibleTopology:
    movable_entities = exact._movable_entity_ids(state.circuit, options)
    initial_movable_count = len(movable_entities) + len(state.relay_positions)
    iterations = options.iterations
    if iterations is None:
        iterations = 0 if initial_movable_count < 6 else min(20_000, 30 * initial_movable_count)
    if iterations <= 0 or initial_movable_count == 0:
        return topology

    center = _centroid([*state.positions.values(), *state.relay_positions.values()])
    unit_sites = set(grid.unit_slots)
    wide_sites = set(grid.slots)
    occupancy = _SpatialOccupancy.build(state)
    rng = Random(options.random_seed ^ 0x61A7E5ED)

    best_score = _exact_score(state, topology, center)
    best_positions = dict(state.positions)
    best_relays = dict(state.relay_positions)
    best_relay_groups = dict(state.relay_groups)
    best_routing = topology.routing

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
                if not _position_is_legal(state, candidate, current, unit_sites, wide_sites):
                    continue
                other = candidate

            if not _position_is_legal(state, object_id, target, unit_sites, wide_sites):
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
            topology.total_energy += wire_delta

        topology = _simplify_feasible_topology(state, topology)
        occupancy = _SpatialOccupancy.build(state)
        score = _exact_score(state, topology, center)
        if score < best_score:
            best_score = score
            best_positions = dict(state.positions)
            best_relays = dict(state.relay_positions)
            best_relay_groups = dict(state.relay_groups)
            best_routing = topology.routing

    state.positions.clear()
    state.positions.update(best_positions)
    state.relay_positions.clear()
    state.relay_positions.update(best_relays)
    state.relay_groups.clear()
    state.relay_groups.update(best_relay_groups)
    return _FeasibleTopology.build(state, best_routing)


def _exact_score(
    state: exact._JointState,
    topology: _FeasibleTopology,
    center: Position,
) -> tuple[int, float]:
    movable_objects = [*state.positions, *state.relay_positions]
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
    current: Position,
    rng: Random,
    normalized_temperature: float,
) -> Position:
    if object_id in state.relay_positions:
        if rng.random() < 0.08:
            return grid.unit_slots[rng.randrange(len(grid.unit_slots))]
        if rng.random() < 0.35:
            noise = max(1.0, state.safe_span * (0.8 * normalized_temperature + 0.05))
            target = (
                current[0] + rng.uniform(-noise, noise),
                current[1] + rng.uniform(-noise, noise),
            )
        else:
            noise = state.safe_span * (normalized_temperature + 0.03)
            target = (
                preferred[0] + rng.uniform(-noise, noise),
                preferred[1] + rng.uniform(-noise, noise),
            )
        x = min(grid.unit_x_positions, key=lambda value: (abs(value - target[0]), value))
        y = min(grid.y_positions, key=lambda value: (abs(value - target[1]), value))
        return (x, y)

    entity = state.circuit.entity_by_id(object_id)
    candidates = base_placement._candidate_positions(entity, grid)
    if rng.random() < 0.08:
        return candidates[rng.randrange(len(candidates))]
    if rng.random() < 0.35:
        noise = max(1.0, state.safe_span * (0.8 * normalized_temperature + 0.05))
        target = (
            current[0] + rng.uniform(-noise, noise),
            current[1] + rng.uniform(-noise, noise),
        )
    else:
        noise = state.safe_span * (normalized_temperature + 0.03)
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
