"""Reach-safe joint annealing for implementation entities and wire relays.

The annealer starts from an explicitly reach-safe routed topology. Ordinary proposals are allowed to
move implementation combinators and relay combinators, but a proposal is rejected immediately if it
would make any cached incident wire exceed the configured safe span. This keeps the hot loop local:
proposal work scales with the moved objects' topology degree, not with the size of their physical
nets.

At epoch boundaries the implementation may spend O(k^2) work on a physical net: optional relays are
pruned and a fresh exact reach-safe spanning tree is built. The optimizer therefore compacts a
feasible layout instead of hoping an infeasible Euclidean surrogate can later be repaired.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from heapq import heappop, heappush
from math import ceil, exp, floor, hypot
from random import Random

from factorio_circuit.blueprint import routing as wire_routing
from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import PhysicalCircuit, WireColor
from factorio_circuit.synthesis import joint_layout as exact
from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.layout import Layout
from factorio_circuit.synthesis.placement import PlacementOptions, Position
from factorio_circuit.synthesis.safe_folded_crossbar import build_safe_folded_crossbar_layout

_EPOCH_PROPOSALS = 256
_EPSILON = 1e-9
_RELAY_HALF_EXTENT = (0.5, 0.5)
_SLACK_START = 0.85
Bucket = tuple[int, int]
Node = tuple[int, int]


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
        half = self.state.object_half_extent(object_id)
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


@dataclass(frozen=True, slots=True)
class _MoveGeometry:
    """Finite lattice envelope used for reach-preserving compaction moves."""

    left: float
    right: float
    top: float
    bottom: float
    phases: dict[int, tuple[float, float]]

    @classmethod
    def from_state(cls, state: exact._JointState) -> _MoveGeometry:
        object_ids = [*state.positions, *state.relay_positions]
        points = [state.object_position(object_id) for object_id in object_ids]
        if not points:
            return cls(0.0, 0.0, 0.0, 0.0, {})
        margin = state.safe_span
        return cls(
            min(x for x, _ in points) - margin,
            max(x for x, _ in points) + margin,
            min(y for _, y in points) - margin,
            max(y for _, y in points) + margin,
            {
                object_id: (
                    _fractional_phase(state.object_position(object_id)[0]),
                    _fractional_phase(state.object_position(object_id)[1]),
                )
                for object_id in object_ids
            },
        )

    def nearest(self, object_id: int, point: Position) -> Position:
        phase_x, phase_y = self.phases[object_id]
        min_x = ceil(self.left - phase_x - _EPSILON)
        max_x = floor(self.right - phase_x + _EPSILON)
        min_y = ceil(self.top - phase_y - _EPSILON)
        max_y = floor(self.bottom - phase_y + _EPSILON)
        x_index = min(max_x, max(min_x, round(point[0] - phase_x)))
        y_index = min(max_y, max(min_y, round(point[1] - phase_y)))
        return (float(x_index) + phase_x, float(y_index) + phase_y)

    def random(self, object_id: int, rng: Random) -> Position:
        phase_x, phase_y = self.phases[object_id]
        min_x = ceil(self.left - phase_x - _EPSILON)
        max_x = floor(self.right - phase_x + _EPSILON)
        min_y = ceil(self.top - phase_y - _EPSILON)
        max_y = floor(self.bottom - phase_y + _EPSILON)
        return (
            float(rng.randint(min_x, max_x)) + phase_x,
            float(rng.randint(min_y, max_y)) + phase_y,
        )

    def position_is_legal(self, object_id: int, position: Position) -> bool:
        phase_x, phase_y = self.phases[object_id]
        return (
            self.left - _EPSILON <= position[0] <= self.right + _EPSILON
            and self.top - _EPSILON <= position[1] <= self.bottom + _EPSILON
            and abs(_fractional_phase(position[0]) - phase_x) <= _EPSILON
            and abs(_fractional_phase(position[1]) - phase_y) <= _EPSILON
        )


def _wire_energy(distance: float, safe_span: float) -> float:
    """Prefer short wires and preserve slack well before the hard reach boundary."""

    normalized = distance / safe_span
    slack_pressure = max(0.0, normalized - _SLACK_START)
    return 0.12 * normalized + 4.0 * slack_pressure**2


def _routing_from_layout(layout: Layout) -> wire_routing.RoutingPlan:
    return wire_routing.RoutingPlan(
        relays=tuple(
            wire_routing.BlueprintRelay(relay.entity_id, relay.position, relay.description)
            for relay in layout.relays
        ),
        wires=tuple(
            wire_routing.RoutedWire(
                wire.source_entity,
                wire.source_connector_id,
                wire.target_entity,
                wire.target_connector_id,
                wire.color,
            )
            for wire in layout.wires
        ),
    )


def _infer_relay_groups(
    state: exact._JointState,
    routing: wire_routing.RoutingPlan,
) -> None:
    """Infer each bootstrap relay's physical net from connector-level wire components."""

    parent: dict[Node, Node] = {}

    def find(node: Node) -> Node:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: Node, right: Node) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for wire in routing.wires:
        union(
            (wire.source_entity, wire.source_connector_id),
            (wire.target_entity, wire.target_connector_id),
        )

    root_groups: dict[Node, int] = {}
    for group, endpoints in state.endpoints_by_group.items():
        color = state.colors_by_group[group]
        for endpoint in endpoints:
            entity_id, connector_id = exact._vertex_connector(
                state,
                exact._terminal_vertex(endpoint),
                color,
            )
            root = find((entity_id, connector_id))
            previous = root_groups.setdefault(root, group)
            if previous != group:
                raise ValueError(
                    "bootstrap routing joins distinct physical net groups "
                    f"{previous} and {group}"
                )

    relay_nodes: dict[int, set[Node]] = defaultdict(set)
    for wire in routing.wires:
        if wire.source_entity in state.relay_positions:
            relay_nodes[wire.source_entity].add(
                find((wire.source_entity, wire.source_connector_id))
            )
        if wire.target_entity in state.relay_positions:
            relay_nodes[wire.target_entity].add(
                find((wire.target_entity, wire.target_connector_id))
            )

    for relay_id in sorted(state.relay_positions):
        groups = {
            root_groups[root]
            for root in relay_nodes.get(relay_id, ())
            if root in root_groups
        }
        if len(groups) != 1:
            raise ValueError(
                f"could not infer exactly one physical net for bootstrap relay {relay_id}"
            )
        state.relay_groups[relay_id] = next(iter(groups))


def _state_from_bootstrap_layout(
    circuit: PhysicalCircuit,
    endpoints_by_group: dict[int, tuple[abstract.Endpoint, ...]],
    colors_by_group: dict[int, WireColor],
    layout: Layout,
    *,
    safe_wire_span: float,
) -> tuple[exact._JointState, _FeasibleTopology]:
    implementation_ids = {entity.id for entity in circuit.entities}
    missing = implementation_ids - set(layout.positions)
    if missing:
        raise ValueError(f"reach-safe bootstrap is missing implementation entities {sorted(missing)}")

    state = exact._JointState(
        circuit=circuit,
        endpoints_by_group=endpoints_by_group,
        colors_by_group=colors_by_group,
        positions={entity_id: layout.positions[entity_id] for entity_id in implementation_ids},
        relay_positions={relay.entity_id: relay.position for relay in layout.relays},
        relay_groups={},
        safe_span=safe_wire_span,
        forbidden_areas=(),
    )
    routing = _routing_from_layout(layout)
    _infer_relay_groups(state, routing)
    _validate_joint_clearance(circuit, state.positions, routing)
    return state, _FeasibleTopology.build(state, routing)


def _construct_feasible_bootstrap(
    state: exact._JointState,
    grid: base_placement._GridGeometry,
) -> _FeasibleTopology:
    """Construct a reach-safe bootstrap on the candidate grid for anchored/small callers.

    Production unanchored annealing may fall back to the proven folded-safe layout instead. This
    constructor is a one-time bootstrap operation; the annealing loop never repairs an infeasible
    state.
    """

    if all(exact._group_spanning_tree(state, group) is not None for group in state.endpoints_by_group):
        routing = exact._routing_plan(state)
        return _FeasibleTopology.build(state, routing)

    free_sites = {
        site
        for site in grid.unit_slots
        if not any(
            base_placement._boxes_overlap(
                site,
                _RELAY_HALF_EXTENT,
                state.object_position(object_id),
                state.object_half_extent(object_id),
            )
            for object_id in [*state.positions, *state.relay_positions]
        )
    }
    next_relay_id = max((entity.id for entity in state.circuit.entities), default=0) + 1

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

    for _negative_overreach, group, edges in sorted(group_order):
        if exact._group_spanning_tree(state, group) is not None:
            continue
        for left, right in edges:
            source = state.vertex_position(left)
            target = state.vertex_position(right)
            if _distance(source, target) <= state.safe_span + _EPSILON:
                continue
            chain = _find_relay_chain(state, group, left[1], right[1], free_sites)
            if chain is None:
                raise ValueError(
                    "could not construct an initial reach-safe joint topology on the candidate grid"
                )
            for position in chain:
                relay_id = next_relay_id
                next_relay_id += 1
                state.relay_positions[relay_id] = position
                state.relay_groups[relay_id] = group
                free_sites.discard(position)

        if exact._group_spanning_tree(state, group) is None:
            raise ValueError(
                f"could not construct an initial reach-safe topology for physical net {group}"
            )

    _prune_relays_to_terminal_paths(state)
    routing = exact._routing_plan(state)
    return _FeasibleTopology.build(state, routing)


def _find_relay_chain(
    state: exact._JointState,
    group: int,
    left: int,
    right: int,
    free_sites: set[Position],
) -> tuple[Position, ...] | None:
    """Find a minimum-new-relay path through legal vacant unit sites."""

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
        positions[index] for index in path if index != goal_index and new_relay_cost[index] == 1
    )


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


def _refresh_feasible_topology(state: exact._JointState) -> _FeasibleTopology:
    """Prune optional relays and rebuild an exact reach-safe tree for every net."""

    _prune_relays_to_terminal_paths(state)
    routing = exact._routing_plan(state)
    return _FeasibleTopology.build(state, routing)


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
    bootstrap_layout: Layout | None = None,
) -> exact.JointLayoutResult:
    """Compact a reach-safe joint layout while preserving wire reach on every accepted move."""

    endpoints_by_group, colors_by_group = exact._physical_groups(
        abstract_circuit,
        net_groups,
        net_colors,
    )

    if bootstrap_layout is not None:
        state, topology = _state_from_bootstrap_layout(
            circuit,
            endpoints_by_group,
            colors_by_group,
            bootstrap_layout,
            safe_wire_span=safe_wire_span,
        )
    else:
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
        try:
            topology = _construct_feasible_bootstrap(state, grid)
        except ValueError:
            if options.anchors:
                raise
            bootstrap_layout = build_safe_folded_crossbar_layout(
                abstract_circuit,
                circuit,
                net_colors=net_colors,
                net_groups=net_groups,
                signal_allocation={},
                safe_wire_span=safe_wire_span,
                progress=None,
            )
            state, topology = _state_from_bootstrap_layout(
                circuit,
                endpoints_by_group,
                colors_by_group,
                bootstrap_layout,
                safe_wire_span=safe_wire_span,
            )

    topology = _anneal_feasible(state, topology, options)
    routing = topology.routing
    all_positions = dict(state.positions)
    all_positions.update(state.relay_positions)
    wire_routing.validate_wire_spans(
        routing.wires,
        all_positions,
        maximum_span=safe_wire_span,
    )
    _validate_joint_clearance(circuit, state.positions, routing)
    return exact.JointLayoutResult(dict(state.positions), routing)


def _anneal_feasible(
    state: exact._JointState,
    topology: _FeasibleTopology,
    options: PlacementOptions,
) -> _FeasibleTopology:
    movable_entities = exact._movable_entity_ids(state.circuit, options)
    initial_movable_count = len(movable_entities) + len(state.relay_positions)
    iterations = options.iterations
    if iterations is None:
        iterations = 0 if initial_movable_count < 6 else min(20_000, 30 * initial_movable_count)
    if iterations <= 0 or initial_movable_count == 0:
        return topology

    center = _centroid([*state.positions.values(), *state.relay_positions.values()])
    geometry = _MoveGeometry.from_state(state)
    occupancy = _SpatialOccupancy.build(state)
    rng = Random(options.random_seed ^ 0x61A7E5ED)

    best_score = _exact_score(state, topology, center)
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
                geometry,
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
                if not geometry.position_is_legal(candidate, current):
                    continue
                other = candidate

            if not geometry.position_is_legal(object_id, target):
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

        topology = _refresh_feasible_topology(state)
        occupancy = _SpatialOccupancy.build(state)
        score = _exact_score(state, topology, center)
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
    return _refresh_feasible_topology(state)


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
    geometry: _MoveGeometry,
    preferred: Position,
    current: Position,
    rng: Random,
    normalized_temperature: float,
) -> Position:
    if rng.random() < 0.08:
        return geometry.random(object_id, rng)

    if rng.random() < 0.35:
        noise = max(1.0, state.safe_span * (0.8 * normalized_temperature + 0.05))
        target = (
            current[0] + rng.uniform(-noise, noise),
            current[1] + rng.uniform(-noise, noise),
        )
        return geometry.nearest(object_id, target)

    noise = state.safe_span * (1.0 * normalized_temperature + 0.03)
    target = (
        preferred[0] + rng.uniform(-noise, noise),
        preferred[1] + rng.uniform(-noise, noise),
    )
    return geometry.nearest(object_id, target)


def _fractional_phase(value: float) -> float:
    phase = value - floor(value)
    return 0.0 if abs(phase) <= _EPSILON or abs(phase - 1.0) <= _EPSILON else phase


def _centroid(points: list[Position]) -> Position:
    if not points:
        return (0.0, 0.0)
    return (
        sum(x for x, _ in points) / len(points),
        sum(y for _, y in points) / len(points),
    )


def _distance(left: Position, right: Position) -> float:
    return hypot(left[0] - right[0], left[1] - right[1])
