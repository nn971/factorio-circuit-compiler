"""Joint annealed refinement of implementation combinators and layout-only relays.

The ordinary placer supplies the implementation-entity seed. This stage realizes each synthesized
physical net as one reach-connected graph, introduces the relays needed by that graph, and jointly
refines only relay-bearing nets. A relay belongs to an electrical net group rather than to one
logical point-to-point edge, so it may be a branch point for several terminals. Because an empty
constant combinator keeps its red and green circuit networks electrically separate, one physical
relay may also serve one group of each color.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil, exp, hypot
from random import Random

from factorio_circuit.blueprint import routing as wire_routing
from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import Connector, PhysicalCircuit, WireColor, WireEndpoint
from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.placement import PlacementOptions, Position, RelayForbiddenArea

_RELAY_HALF_EXTENT = (0.5, 0.5)
_CONNECTOR_ORDER = {
    abstract.Connector.SINGLE: 0,
    abstract.Connector.INPUT: 1,
    abstract.Connector.OUTPUT: 2,
}
Vertex = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class JointLayoutResult:
    """Implementation positions plus one shared relay tree per physical net."""

    positions: dict[int, Position]
    routing: wire_routing.RoutingPlan


@dataclass(slots=True)
class _JointState:
    circuit: PhysicalCircuit
    endpoints_by_group: dict[int, tuple[abstract.Endpoint, ...]]
    colors_by_group: dict[int, WireColor]
    positions: dict[int, Position]
    relay_positions: dict[int, Position]
    relay_groups: dict[int, frozenset[int]]
    safe_span: float
    forbidden_areas: tuple[RelayForbiddenArea, ...]

    def group_vertices(self, group: int) -> tuple[Vertex, ...]:
        terminals = tuple(_terminal_vertex(item) for item in self.endpoints_by_group[group])
        relays = tuple(
            _relay_vertex(relay_id)
            for relay_id in sorted(self.relay_positions)
            if group in self.relay_groups[relay_id]
        )
        return (*terminals, *relays)

    def vertex_position(self, vertex: Vertex) -> Position:
        return self.positions[vertex[1]] if vertex[0] == 0 else self.relay_positions[vertex[1]]

    def object_position(self, object_id: int) -> Position:
        if object_id in self.relay_positions:
            return self.relay_positions[object_id]
        return self.positions[object_id]

    def object_half_extent(self, object_id: int) -> tuple[float, float]:
        if object_id in self.relay_positions:
            return _RELAY_HALF_EXTENT
        return base_placement._entity_half_extent(self.circuit.entity_by_id(object_id))


def refine_joint_layout(
    circuit: PhysicalCircuit,
    abstract_circuit: abstract.AbstractPhysicalCircuit,
    net_groups: dict[int, int],
    net_colors: dict[int, WireColor],
    positions: dict[int, Position],
    *,
    safe_wire_span: float,
    options: PlacementOptions,
    relay_forbidden_areas: tuple[RelayForbiddenArea, ...] = (),
) -> JointLayoutResult:
    """Jointly refine implementation entities and relays and emit shared-net routing."""

    endpoints_by_group, colors_by_group = _physical_groups(
        abstract_circuit,
        net_groups,
        net_colors,
    )
    state = _seed_state(
        circuit,
        endpoints_by_group,
        colors_by_group,
        positions,
        safe_wire_span=safe_wire_span,
        forbidden_areas=relay_forbidden_areas,
    )

    _prune_relays(state)
    _joint_anneal(state, options)
    _prune_relays(state)

    routing = _routing_plan(state)
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
        relay_forbidden_areas=relay_forbidden_areas,
    )
    return JointLayoutResult(dict(state.positions), routing)


def _physical_groups(
    circuit: abstract.AbstractPhysicalCircuit,
    net_groups: dict[int, int],
    net_colors: dict[int, WireColor],
) -> tuple[dict[int, tuple[abstract.Endpoint, ...]], dict[int, WireColor]]:
    endpoint_sets: dict[int, set[abstract.Endpoint]] = defaultdict(set)
    colors: dict[int, WireColor] = {}
    for net in circuit.nets:
        group = net_groups[net.id]
        endpoint_sets[group].update(net.endpoints)
        color = net_colors[net.id]
        previous = colors.setdefault(group, color)
        if previous != color:
            raise AssertionError(f"physical net group {group} contains multiple wire colors")
    return (
        {group: tuple(sorted(items)) for group, items in endpoint_sets.items()},
        colors,
    )


def _seed_state(
    circuit: PhysicalCircuit,
    endpoints_by_group: dict[int, tuple[abstract.Endpoint, ...]],
    colors_by_group: dict[int, WireColor],
    positions: dict[int, Position],
    *,
    safe_wire_span: float,
    forbidden_areas: tuple[RelayForbiddenArea, ...],
) -> _JointState:
    relay_positions: dict[int, Position] = {}
    relay_groups: dict[int, frozenset[int]] = {}
    next_relay_id = max((entity.id for entity in circuit.entities), default=0) + 1
    occupied = [
        (positions[entity.id], wire_routing._entity_half_extent(entity), entity.id)
        for entity in circuit.entities
    ]

    groups_needing_relays = [
        group
        for group, endpoints in endpoints_by_group.items()
        if _terminal_reach_tree(endpoints, positions, safe_wire_span) is None
    ]
    edge_total = sum(max(0, len(endpoints_by_group[group]) - 1) for group in groups_needing_relays)
    edge_index = 0

    for group in sorted(groups_needing_relays):
        endpoints = endpoints_by_group[group]
        for left, right in _terminal_metric_mst(endpoints, positions):
            edge_index += 1
            source = positions[left.entity]
            target = positions[right.entity]
            if _distance(source, target) <= safe_wire_span + 1e-9:
                continue
            candidates = wire_routing._find_relay_positions(
                source,
                target,
                safe_span=safe_wire_span,
                occupied=occupied,
                edge_index=edge_index,
                edge_total=max(1, edge_total),
                forbidden_areas=forbidden_areas,
            )
            for candidate in candidates:
                relay_id = next_relay_id
                next_relay_id += 1
                relay_positions[relay_id] = candidate
                relay_groups[relay_id] = frozenset({group})
                occupied.append((candidate, _RELAY_HALF_EXTENT, relay_id))

    state = _JointState(
        circuit=circuit,
        endpoints_by_group=endpoints_by_group,
        colors_by_group=colors_by_group,
        positions=dict(positions),
        relay_positions=relay_positions,
        relay_groups=relay_groups,
        safe_span=safe_wire_span,
        forbidden_areas=forbidden_areas,
    )
    disconnected = [
        group for group in endpoints_by_group if _group_spanning_tree(state, group) is None
    ]
    if disconnected:
        raise ValueError(
            f"joint relay seed left physical net group(s) disconnected: {disconnected}"
        )
    return state


def _terminal_reach_tree(
    endpoints: tuple[abstract.Endpoint, ...],
    positions: dict[int, Position],
    safe_span: float,
) -> tuple[tuple[tuple[abstract.Endpoint, abstract.Endpoint], ...], float] | None:
    def position(item: abstract.Endpoint) -> Position:
        return positions[item.entity]

    return _prim_tree(endpoints, position, maximum_span=safe_span)


def _terminal_metric_mst(
    endpoints: tuple[abstract.Endpoint, ...],
    positions: dict[int, Position],
) -> tuple[tuple[abstract.Endpoint, abstract.Endpoint], ...]:
    def position(item: abstract.Endpoint) -> Position:
        return positions[item.entity]

    tree = _prim_tree(endpoints, position, maximum_span=None)
    assert tree is not None
    return tree[0]


def _prim_tree[T](
    vertices: tuple[T, ...],
    position: Callable[[T], Position],
    *,
    maximum_span: float | None,
) -> tuple[tuple[tuple[T, T], ...], float] | None:
    """Build a deterministic Euclidean Prim tree in O(k^2)."""

    if len(vertices) < 2:
        return ((), 0.0)

    remaining = set(range(1, len(vertices)))
    best: dict[int, tuple[float, int]] = {}

    def consider(left_index: int, right_index: int) -> None:
        distance = _distance(position(vertices[left_index]), position(vertices[right_index]))
        if maximum_span is not None and distance > maximum_span + 1e-9:
            return
        candidate = (distance, left_index)
        previous = best.get(right_index)
        if previous is None or candidate < previous:
            best[right_index] = candidate

    for right_index in remaining:
        consider(0, right_index)

    edges: list[tuple[T, T]] = []
    total_length = 0.0
    while remaining:
        choices = [
            (distance, vertices[left], vertices[right], left, right)
            for right, (distance, left) in best.items()
            if right in remaining
        ]
        if not choices:
            return None
        distance, _left_key, _right_key, left_index, right_index = min(choices)
        edges.append((vertices[left_index], vertices[right_index]))
        total_length += distance
        remaining.remove(right_index)
        best.pop(right_index, None)
        for candidate_index in remaining:
            consider(right_index, candidate_index)

    return (tuple(edges), total_length)


def _joint_anneal(state: _JointState, options: PlacementOptions) -> None:
    if not state.relay_positions or options.iterations == 0:
        return

    relay_groups = {group for groups in state.relay_groups.values() for group in groups}
    active_entities = {
        endpoint.entity for group in relay_groups for endpoint in state.endpoints_by_group[group]
    }
    movable_entities = [
        entity_id
        for entity_id in _movable_entity_ids(state.circuit, options)
        if entity_id in active_entities
    ]
    movable_objects = [*movable_entities, *sorted(state.relay_positions)]
    movable_set = set(movable_objects)
    if len(movable_objects) < 2:
        return

    iterations = (
        min(10_000, 10 * len(movable_objects)) if options.iterations is None else options.iterations
    )
    if iterations <= 0:
        return

    grid = _matching_candidate_grid(state.circuit, state.positions, options)
    relay_bounds = _relay_move_bounds(state)
    incident_groups = _incident_groups(state.endpoints_by_group)
    group_lengths = {
        group: _required_group_length(state, group) for group in state.endpoints_by_group
    }
    center = _centroid([*state.positions.values(), *state.relay_positions.values()])
    rng = Random(options.random_seed ^ 0x5EED5EED)

    best_energy = sum(group_lengths.values()) + _object_compactness(state, movable_objects, center)
    current_energy = best_energy
    best_positions = dict(state.positions)
    best_relays = dict(state.relay_positions)

    for step in range(iterations):
        progress = step / max(1, iterations - 1)
        normalized_temperature = 0.03**progress
        temperature = max(0.02, state.safe_span * (0.8 * normalized_temperature + 0.01))
        object_id = movable_objects[rng.randrange(len(movable_objects))]
        current = state.object_position(object_id)
        target = _proposed_position(
            state,
            object_id,
            grid,
            relay_bounds,
            incident_groups,
            center,
            rng,
            normalized_temperature,
        )
        if target == current:
            continue

        other = _exact_occupant(state, target, ignore_id=object_id)
        # A relay may sit on any half-tile coordinate while an implementation constant must remain
        # on a legal 1x1 grid subslot. Cross-class swaps would silently move the constant outside
        # its legal candidate lattice, so keep relay/entity exchanges as two ordinary moves.
        if other is not None and _is_relay(state, other) != _is_relay(state, object_id):
            continue
        if other is not None and state.object_half_extent(other) != state.object_half_extent(
            object_id
        ):
            continue
        if other is not None and other not in movable_set:
            continue
        if not _move_is_collision_free(state, object_id, target, other):
            continue

        moved = {object_id} if other is None else {object_id, other}
        affected = _object_groups(state, object_id, incident_groups)
        if other is not None:
            affected.update(_object_groups(state, other, incident_groups))
        before = sum(group_lengths[group] for group in affected)
        before += sum(_compactness(state.object_position(item), center) for item in moved)

        _apply_move(state, object_id, target, other)
        updated: dict[int, float] = {}
        feasible = True
        for group in affected:
            tree = _group_spanning_tree(state, group)
            if tree is None:
                feasible = False
                break
            updated[group] = tree[1]

        if feasible:
            after = sum(updated.values())
            after += sum(_compactness(state.object_position(item), center) for item in moved)
            delta = after - before
            accepted = delta <= 0 or rng.random() < exp(-delta / temperature)
        else:
            delta = 0.0
            accepted = False

        if accepted:
            for group, length in updated.items():
                group_lengths[group] = length
            current_energy += delta
            if current_energy + 1e-12 < best_energy:
                best_energy = current_energy
                best_positions = dict(state.positions)
                best_relays = dict(state.relay_positions)
            continue

        _undo_move(state, object_id, current, other, target)

    state.positions.clear()
    state.positions.update(best_positions)
    state.relay_positions.clear()
    state.relay_positions.update(best_relays)


def _matching_candidate_grid(
    circuit: PhysicalCircuit,
    positions: dict[int, Position],
    options: PlacementOptions,
) -> base_placement._GridGeometry:
    input_ids = {port.marker_entity for port in circuit.inputs}
    output_ids = {port.marker_entity for port in circuit.outputs}
    io_ids = input_ids | output_ids
    body_ids = [
        entity.id for entity in circuit.entities if not options.anchor_io or entity.id not in io_ids
    ]
    entities = {entity.id: entity for entity in circuit.entities}
    tile_demand = sum(
        1 if base_placement._entity_half_extent(entities[entity_id])[0] == 0.5 else 2
        for entity_id in body_ids
    )
    body_count = max(1, ceil(tile_demand / 2))
    minimum_rows = max(len(circuit.inputs), len(circuit.outputs), 1) if options.anchor_io else 1

    while True:
        grid = base_placement._candidate_grid(body_count, minimum_rows, options)
        if all(
            entity_id in options.anchors
            or (options.anchor_io and entity_id in io_ids)
            or positions[entity_id]
            in base_placement._candidate_positions(entities[entity_id], grid)
            for entity_id in positions
        ):
            return grid
        body_count += max(4, ceil(max(1, len(body_ids)) / 8))
        if body_count > max(1, ceil(tile_demand / 2)) + 2048:
            raise ValueError("could not recover annealed candidate grid for joint relay refinement")


def _movable_entity_ids(circuit: PhysicalCircuit, options: PlacementOptions) -> list[int]:
    anchored = set(options.anchors)
    if options.anchor_io:
        anchored.update(port.marker_entity for port in circuit.inputs)
        anchored.update(port.marker_entity for port in circuit.outputs)
    return [entity.id for entity in circuit.entities if entity.id not in anchored]


def _relay_move_bounds(state: _JointState) -> tuple[float, float, float, float]:
    points = [*state.positions.values(), *state.relay_positions.values()]
    return (
        min(x for x, _ in points) - state.safe_span,
        max(x for x, _ in points) + state.safe_span,
        min(y for _, y in points) - state.safe_span,
        max(y for _, y in points) + state.safe_span,
    )


def _proposed_position(
    state: _JointState,
    object_id: int,
    grid: base_placement._GridGeometry,
    relay_bounds: tuple[float, float, float, float],
    incident_groups: dict[int, set[int]],
    center: Position,
    rng: Random,
    normalized_temperature: float,
) -> Position:
    preferred = _preferred_object_position(state, object_id, incident_groups, center)
    if _is_relay(state, object_id):
        if rng.random() < 0.15:
            raw = (
                rng.uniform(relay_bounds[0], relay_bounds[1]),
                rng.uniform(relay_bounds[2], relay_bounds[3]),
            )
        else:
            noise = state.safe_span * (1.5 * normalized_temperature + 0.15)
            raw = (
                preferred[0] + rng.uniform(-noise, noise),
                preferred[1] + rng.uniform(-noise, noise),
            )
        return (round(raw[0] * 2) / 2, round(raw[1] * 2) / 2)

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


def _preferred_object_position(
    state: _JointState,
    object_id: int,
    incident_groups: dict[int, set[int]],
    fallback: Position,
) -> Position:
    peers: list[Position] = []
    for group in _object_groups(state, object_id, incident_groups):
        for vertex in state.group_vertices(group):
            if vertex[1] == object_id:
                continue
            peers.append(state.vertex_position(vertex))
    return _centroid(peers) if peers else fallback


def _object_groups(
    state: _JointState,
    object_id: int,
    incident_groups: dict[int, set[int]],
) -> set[int]:
    if object_id in state.relay_groups:
        return set(state.relay_groups[object_id])
    return set(incident_groups.get(object_id, set()))


def _incident_groups(
    endpoints_by_group: dict[int, tuple[abstract.Endpoint, ...]],
) -> dict[int, set[int]]:
    result: dict[int, set[int]] = defaultdict(set)
    for group, endpoints in endpoints_by_group.items():
        for endpoint in endpoints:
            result[endpoint.entity].add(group)
    return dict(result)


def _is_relay(state: _JointState, object_id: int) -> bool:
    return object_id in state.relay_positions


def _exact_occupant(state: _JointState, position: Position, *, ignore_id: int) -> int | None:
    for entity_id, candidate in state.positions.items():
        if entity_id != ignore_id and candidate == position:
            return entity_id
    for relay_id, candidate in state.relay_positions.items():
        if relay_id != ignore_id and candidate == position:
            return relay_id
    return None


def _move_is_collision_free(
    state: _JointState,
    object_id: int,
    target: Position,
    other: int | None,
) -> bool:
    if _is_relay(state, object_id) and wire_routing._relay_overlaps_forbidden(
        target,
        state.forbidden_areas,
    ):
        return False
    ignored = {object_id} if other is None else {object_id, other}
    if not _position_clear_of_objects(state, object_id, target, ignored):
        return False
    if other is None:
        return True

    other_target = state.object_position(object_id)
    if _is_relay(state, other) and wire_routing._relay_overlaps_forbidden(
        other_target,
        state.forbidden_areas,
    ):
        return False
    return _position_clear_of_objects(state, other, other_target, ignored)


def _position_clear_of_objects(
    state: _JointState,
    object_id: int,
    position: Position,
    ignored: set[int],
) -> bool:
    half = state.object_half_extent(object_id)
    object_is_relay = _is_relay(state, object_id)
    for other_id, other_position in state.positions.items():
        if other_id in ignored:
            continue
        other_half = state.object_half_extent(other_id)
        overlap = (
            wire_routing._boxes_overlap(position, half, other_position, other_half)
            if object_is_relay
            else base_placement._boxes_overlap(position, half, other_position, other_half)
        )
        if overlap:
            return False
    for relay_id, relay_position in state.relay_positions.items():
        if relay_id in ignored:
            continue
        if wire_routing._boxes_overlap(position, half, relay_position, _RELAY_HALF_EXTENT):
            return False
    return True


def _apply_move(
    state: _JointState,
    object_id: int,
    target: Position,
    other: int | None,
) -> None:
    current = state.object_position(object_id)
    _set_object_position(state, object_id, target)
    if other is not None:
        _set_object_position(state, other, current)


def _undo_move(
    state: _JointState,
    object_id: int,
    current: Position,
    other: int | None,
    target: Position,
) -> None:
    _set_object_position(state, object_id, current)
    if other is not None:
        _set_object_position(state, other, target)


def _set_object_position(state: _JointState, object_id: int, position: Position) -> None:
    if _is_relay(state, object_id):
        state.relay_positions[object_id] = position
    else:
        state.positions[object_id] = position


def _prune_relays(state: _JointState) -> None:
    # One pass is sufficient. If one relay membership is essential while all remaining memberships
    # are present, deleting some of those other memberships later cannot make it removable.
    for relay_id in sorted(tuple(state.relay_positions)):
        for group in sorted(state.relay_groups[relay_id]):
            previous = state.relay_groups[relay_id]
            remaining = previous - {group}
            if remaining:
                state.relay_groups[relay_id] = remaining
            else:
                del state.relay_groups[relay_id]
                position = state.relay_positions.pop(relay_id)
            if _group_spanning_tree(state, group) is not None:
                continue
            state.relay_groups[relay_id] = previous
            if not remaining:
                state.relay_positions[relay_id] = position


def _required_group_length(state: _JointState, group: int) -> float:
    tree = _group_spanning_tree(state, group)
    if tree is None:
        raise ValueError(f"physical net group {group} is outside conservative wire reach")
    return tree[1]


def _group_spanning_tree(
    state: _JointState,
    group: int,
) -> tuple[tuple[tuple[Vertex, Vertex], ...], float] | None:
    vertices = state.group_vertices(group)

    def position(vertex: Vertex) -> Position:
        return state.vertex_position(vertex)

    return _prim_tree(vertices, position, maximum_span=state.safe_span)


def _routing_plan(state: _JointState) -> wire_routing.RoutingPlan:
    relays = tuple(
        wire_routing.BlueprintRelay(
            entity_id=relay_id,
            position=state.relay_positions[relay_id],
            description=(
                "WIRE RELAY — layout-only ("
                + "; ".join(
                    f"{state.colors_by_group[group].value} net {group}"
                    for group in sorted(state.relay_groups[relay_id])
                )
                + ")"
            ),
        )
        for relay_id in sorted(state.relay_positions)
    )

    wires: list[wire_routing.RoutedWire] = []
    for group in sorted(state.endpoints_by_group):
        tree = _group_spanning_tree(state, group)
        if tree is None:
            raise ValueError(f"physical net group {group} became disconnected during refinement")
        color = state.colors_by_group[group]
        wires.extend(_routed_edge(state, left, right, color) for left, right in tree[0])
    return wire_routing.RoutingPlan(relays=relays, wires=tuple(wires))


def _routed_edge(
    state: _JointState,
    left: Vertex,
    right: Vertex,
    color: WireColor,
) -> wire_routing.RoutedWire:
    left_entity, left_connector = _vertex_connector(state, left, color)
    right_entity, right_connector = _vertex_connector(state, right, color)
    return wire_routing.RoutedWire(
        source_entity=left_entity,
        source_connector_id=left_connector,
        target_entity=right_entity,
        target_connector_id=right_connector,
        color=color,
    )


def _vertex_connector(
    state: _JointState,
    vertex: Vertex,
    color: WireColor,
) -> tuple[int, int]:
    if vertex[0] == 1:
        connector = wire_routing._relay_connector_id(color)
        return (vertex[1], wire_routing._colorize_connector(connector, color))

    endpoint = _vertex_endpoint(state, vertex)
    physical_endpoint = WireEndpoint(endpoint.entity, Connector(endpoint.connector.value))
    connector = wire_routing._endpoint_connector_id(state.circuit, physical_endpoint)
    return (endpoint.entity, wire_routing._colorize_connector(connector, color))


def _vertex_endpoint(state: _JointState, vertex: Vertex) -> abstract.Endpoint:
    return next(
        endpoint
        for endpoints in state.endpoints_by_group.values()
        for endpoint in endpoints
        if _terminal_vertex(endpoint) == vertex
    )


def _terminal_vertex(endpoint: abstract.Endpoint) -> Vertex:
    return (0, endpoint.entity, _CONNECTOR_ORDER[endpoint.connector])


def _relay_vertex(relay_id: int) -> Vertex:
    return (1, relay_id, 0)


def _object_compactness(
    state: _JointState,
    object_ids: list[int],
    center: Position,
) -> float:
    return sum(_compactness(state.object_position(object_id), center) for object_id in object_ids)


def _compactness(position: Position, center: Position) -> float:
    return 0.002 * _distance_sq(position, center)


def _centroid(points: list[Position]) -> Position:
    if not points:
        return (0.0, 0.0)
    return (
        sum(x for x, _ in points) / len(points),
        sum(y for _, y in points) / len(points),
    )


def _distance(left: Position, right: Position) -> float:
    return hypot(left[0] - right[0], left[1] - right[1])


def _distance_sq(left: Position, right: Position) -> float:
    return (left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2
