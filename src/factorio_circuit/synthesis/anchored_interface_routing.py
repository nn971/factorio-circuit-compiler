"""Pre-route public-port pinning with fixed relay workspace reservations.

Milestone D3 makes a distant public anchor a physical constraint before global fresh routing. Each
interface binds a named compiler port to one rigid-component access point and an exact external
position. The public marker is moved and fixed first. A deterministic relay chain from that marker
to a terminal of the same electrical group near the declared access point is then constructed and
fixed before every remaining physical net is routed from scratch.

Fixed interface relays are a stronger reservation than a post-hoc waypoint suggestion: ordinary
placement cannot occupy them, relay simplification cannot delete them, and exact layout validation
checks their clearance, wire reach, and electrical membership in the final serialized artifact.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from heapq import heappop, heappush
from math import ceil, floor, hypot
from typing import Literal

from factorio_circuit.ir.physical import (
    ConstantCombinator,
    InputPort,
    OutputPort,
    PhysicalCircuit,
)
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis import joint_layout as exact
from factorio_circuit.synthesis import layout_optimizer
from factorio_circuit.synthesis.component_geometry import (
    ComponentLayoutOptimizationProblem,
    ComponentRegion,
    RigidComponentConstraint,
    lower_component_layout_problem,
    validate_component_layout_problem,
)
from factorio_circuit.synthesis.layout_optimizer import (
    PhysicalLayoutMetrics,
    physical_layout_metrics,
)
from factorio_circuit.synthesis.placement import Position, RelayForbiddenArea
from factorio_circuit.synthesis.rigid_component_translation import (
    _validate_candidate_implementation_positions,
)

PortDirection = Literal["input", "output"]
_EPSILON = 1e-9
_RELAY_HALF_EXTENT = (0.5, 0.5)


@dataclass(frozen=True, slots=True)
class PublicPortAnchorConstraint:
    """Pin one named public compiler port through one rigid-component access point."""

    name: str
    direction: PortDirection
    port: str
    component: str
    access_point: str
    anchor_position: Position
    max_detour_tiles: int = 4

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("anchored interface name must be non-empty")
        if self.direction not in {"input", "output"}:
            raise ValueError("anchored interface direction must be 'input' or 'output'")
        if not self.port:
            raise ValueError("anchored interface port name must be non-empty")
        if not self.component:
            raise ValueError("anchored interface component name must be non-empty")
        if not self.access_point:
            raise ValueError("anchored interface access point name must be non-empty")
        if self.max_detour_tiles < 0:
            raise ValueError("anchored interface max_detour_tiles must be non-negative")


@dataclass(frozen=True, slots=True)
class AnchoredInterfaceLayoutProblem:
    """A component-aware physical problem plus exact distant public-anchor constraints."""

    component_problem: ComponentLayoutOptimizationProblem
    interfaces: tuple[PublicPortAnchorConstraint, ...]

    def __post_init__(self) -> None:
        if not self.interfaces:
            raise ValueError("anchored interface routing requires at least one interface")
        names = [interface.name for interface in self.interfaces]
        if len(set(names)) != len(names):
            raise ValueError("anchored interface names must be unique")
        ports = [(interface.direction, interface.port) for interface in self.interfaces]
        if len(set(ports)) != len(ports):
            raise ValueError("one public compiler port cannot be anchored more than once")
        positions = [interface.anchor_position for interface in self.interfaces]
        if len(set(positions)) != len(positions):
            raise ValueError("distinct public anchors must occupy distinct positions")


@dataclass(frozen=True, slots=True)
class AnchoredRelayReservation:
    """Fixed relay chain reserved for one public interface before global routing."""

    interface_name: str
    marker_entity: int
    group: int
    landing_entity: int
    anchor_position: Position
    access_position: Position
    landing_position: Position
    relay_ids: tuple[int, ...]
    relay_positions: tuple[Position, ...]

    def __post_init__(self) -> None:
        if len(self.relay_ids) != len(self.relay_positions):
            raise ValueError("anchored relay reservation ids and positions must have equal length")


@dataclass(frozen=True, slots=True)
class AnchoredInterfaceRoutingResult:
    """Exact anchored routing on success, or the original exact-valid problem on failure."""

    problem: AnchoredInterfaceLayoutProblem
    succeeded: bool
    failure: str | None
    reservations: tuple[AnchoredRelayReservation, ...]
    before: PhysicalLayoutMetrics
    after: PhysicalLayoutMetrics
    workspace_sites_considered: int


@dataclass(frozen=True, slots=True)
class _ResolvedInterface:
    constraint: PublicPortAnchorConstraint
    marker_entity: int
    group: int
    component: RigidComponentConstraint
    access_position: Position
    landing_entity: int
    landing_position: Position


def route_anchored_interfaces_transactionally(
    problem: AnchoredInterfaceLayoutProblem,
) -> AnchoredInterfaceRoutingResult:
    """Pin all public anchors, reserve their relay chains, then rebuild global routing.

    The current component-aware artifact must already be exact-valid. The transaction discards every
    old non-interface relay. If any anchor cannot reach its declared component access, if fresh
    routing fails, or if the final artifact violates component/interface geometry, the exact
    original input is returned unchanged.
    """

    component_problem = problem.component_problem
    validate_component_layout_problem(component_problem)
    before = physical_layout_metrics(component_problem.layout_problem.layout)
    try:
        return _route_anchored_interfaces(problem, before)
    except ValueError as exc:
        return AnchoredInterfaceRoutingResult(
            problem,
            False,
            str(exc),
            (),
            before,
            before,
            0,
        )


def validate_anchored_interface_routing(
    problem: AnchoredInterfaceLayoutProblem,
    reservations: tuple[AnchoredRelayReservation, ...],
) -> None:
    """Validate exact pins plus the fixed relay reservations in a routed D3 artifact."""

    validate_component_layout_problem(problem.component_problem)
    lowered = lower_component_layout_problem(problem.component_problem)
    embedding = layout_optimizer._validated_embedding(lowered)
    layout = problem.component_problem.layout_problem.layout
    fixed_positions = problem.component_problem.layout_problem.fixed_positions
    relay_positions = {relay.entity_id: relay.position for relay in layout.relays}
    reservation_by_name = {reservation.interface_name: reservation for reservation in reservations}
    if len(reservation_by_name) != len(reservations):
        raise ValueError("anchored relay reservations contain duplicate interface names")
    if set(reservation_by_name) != {interface.name for interface in problem.interfaces}:
        raise ValueError("anchored relay reservations do not exactly cover declared interfaces")

    for interface in problem.interfaces:
        port = _resolve_public_port(layout.circuit, interface)
        marker = port.marker_entity
        if layout.positions[marker] != interface.anchor_position:
            raise ValueError(f"public port {interface.port!r} is not at its exact anchor position")
        if fixed_positions.get(marker) != interface.anchor_position:
            raise ValueError(f"public port {interface.port!r} is not fixed at its anchor position")

        reservation = reservation_by_name[interface.name]
        if reservation.marker_entity != marker:
            raise ValueError(f"reservation {interface.name!r} refers to the wrong public marker")
        group = _marker_group(embedding.state, marker)
        if reservation.group != group:
            raise ValueError(f"reservation {interface.name!r} refers to the wrong electrical group")

        component = _component_by_name(problem.component_problem, interface.component)
        access_positions = component.access_positions()
        if interface.access_point not in access_positions:
            raise ValueError(
                f"component {component.name!r} has no access point {interface.access_point!r}"
            )
        if reservation.access_position != access_positions[interface.access_point]:
            raise ValueError(f"reservation {interface.name!r} access position is stale")
        if reservation.landing_entity not in component.member_ids:
            raise ValueError(f"reservation {interface.name!r} lands outside its rigid component")
        if layout.positions[reservation.landing_entity] != reservation.landing_position:
            raise ValueError(f"reservation {interface.name!r} landing position is stale")

        for relay_id, relay_position in zip(
            reservation.relay_ids,
            reservation.relay_positions,
            strict=True,
        ):
            if relay_positions.get(relay_id) != relay_position:
                raise ValueError(f"reserved interface relay {relay_id} is missing or moved")
            if fixed_positions.get(relay_id) != relay_position:
                raise ValueError(f"reserved interface relay {relay_id} is not fixed")
            if group not in embedding.state.relay_groups.get(relay_id, frozenset()):
                raise ValueError(f"reserved interface relay {relay_id} is not on its public net")

        path = (
            interface.anchor_position,
            *reservation.relay_positions,
            reservation.landing_position,
        )
        for left, right in zip(path[:-1], path[1:], strict=True):
            if _distance(left, right) > lowered.safe_wire_span + _EPSILON:
                raise ValueError(
                    f"reservation {interface.name!r} contains an overlong interface hop"
                )


def _route_anchored_interfaces(
    problem: AnchoredInterfaceLayoutProblem,
    before: PhysicalLayoutMetrics,
) -> AnchoredInterfaceRoutingResult:
    component_problem = problem.component_problem
    base = component_problem.layout_problem
    old_relay_ids = {relay.entity_id for relay in base.layout.relays}
    fixed_old_relays = old_relay_ids & set(base.fixed_positions)
    if fixed_old_relays:
        raise ValueError(
            "anchored fresh routing cannot preserve pre-existing fixed relay ids: "
            f"{sorted(fixed_old_relays)}"
        )

    current_lowered = lower_component_layout_problem(component_problem)
    baseline_embedding = layout_optimizer._validated_embedding(current_lowered)
    resolved = tuple(
        _resolve_interface(component_problem, baseline_embedding.state, interface)
        for interface in problem.interfaces
    )

    marker_ids = [item.marker_entity for item in resolved]
    if len(set(marker_ids)) != len(marker_ids):
        raise ValueError("anchored interfaces resolved to duplicate public marker entities")

    circuit = base.layout.circuit
    positions = {entity.id: base.layout.positions[entity.id] for entity in circuit.entities}
    fixed_positions = dict(base.fixed_positions)
    member_ids = {
        member_id
        for component in component_problem.components
        for member_id in component.member_ids
    }
    for item in resolved:
        marker = item.marker_entity
        target = item.constraint.anchor_position
        if marker in member_ids and positions[marker] != target:
            raise ValueError(
                f"public marker {marker} belongs to a rigid component and cannot move independently"
            )
        existing = fixed_positions.get(marker)
        if existing is not None and existing != target:
            raise ValueError(
                f"public marker {marker} is already fixed at {existing!r}, not {target!r}"
            )
        positions[marker] = target
        fixed_positions[marker] = target

    provisional_layout = replace(base.layout, positions=positions, relays=(), wires=())
    provisional_base = replace(
        base,
        layout=provisional_layout,
        fixed_positions=fixed_positions,
    )
    provisional_components = replace(
        component_problem,
        layout_problem=provisional_base,
    )
    candidate_lowered = lower_component_layout_problem(
        provisional_components,
        validate_base=False,
    )
    _validate_candidate_implementation_positions(candidate_lowered)

    state = exact._JointState(
        circuit=baseline_embedding.state.circuit,
        endpoints_by_group=baseline_embedding.state.endpoints_by_group,
        colors_by_group=baseline_embedding.state.colors_by_group,
        positions=dict(positions),
        relay_positions={},
        relay_groups={},
        safe_span=baseline_embedding.state.safe_span,
        forbidden_areas=candidate_lowered.lattice.forbidden_areas,
        fixed_objects=frozenset(candidate_lowered.fixed_positions),
    )

    exclusions = _component_exclusion_regions(provisional_components)
    next_relay_id = max((entity.id for entity in circuit.entities), default=0) + 1
    reserved_fixed_ids: set[int] = set()
    reservations: list[AnchoredRelayReservation] = []
    workspace_sites_considered = 0

    for item in resolved:
        if _distance(item.constraint.anchor_position, item.landing_position) <= (
            state.safe_span + _EPSILON
        ):
            reservations.append(
                AnchoredRelayReservation(
                    item.constraint.name,
                    item.marker_entity,
                    item.group,
                    item.landing_entity,
                    item.constraint.anchor_position,
                    item.access_position,
                    item.landing_position,
                    (),
                    (),
                )
            )
            continue

        generated = _interface_workspace_sites(
            item.constraint.anchor_position,
            item.access_position,
            item.constraint.max_detour_tiles,
        )
        workspace_sites_considered += len(generated)
        occupancy = incremental._SpatialOccupancy.build(state)
        free_sites = {
            site
            for site in generated
            if not incremental._box_overlaps_occupancy(
                occupancy,
                site,
                _RELAY_HALF_EXTENT,
                ignored=set(),
            )
            and not _site_overlaps_regions(site, exclusions)
            and not _site_overlaps_forbidden(site, candidate_lowered.lattice.forbidden_areas)
        }
        gateway_candidates = [
            site
            for site in free_sites
            if _distance(site, item.access_position) <= state.safe_span + _EPSILON
            and _distance(site, item.landing_position) <= state.safe_span + _EPSILON
        ]
        if not gateway_candidates:
            raise ValueError(
                f"anchored interface {item.constraint.name!r} has no legal relay gateway near "
                f"access point {item.constraint.access_point!r}"
            )
        gateway = min(
            gateway_candidates,
            key=lambda site: (
                _distance(site, item.access_position),
                _distance(site, item.landing_position),
                site,
            ),
        )

        chain = _find_interface_relay_chain(
            item.constraint.anchor_position,
            gateway,
            free_sites - {gateway},
            state.safe_span,
        )
        if chain is None:
            raise ValueError(
                f"anchored interface {item.constraint.name!r} cannot route from its exact public "
                "anchor to the reserved access workspace"
            )

        path_ids: list[int] = []
        path_positions: list[Position] = []
        for site in (*chain, gateway):
            relay_id = next_relay_id
            next_relay_id += 1
            state.relay_positions[relay_id] = site
            state.relay_groups[relay_id] = frozenset({item.group})
            reserved_fixed_ids.add(relay_id)
            path_ids.append(relay_id)
            path_positions.append(site)
        state.fixed_objects = frozenset((*state.fixed_objects, *path_ids))

        reservations.append(
            AnchoredRelayReservation(
                item.constraint.name,
                item.marker_entity,
                item.group,
                item.landing_entity,
                item.constraint.anchor_position,
                item.access_position,
                item.landing_position,
                tuple(path_ids),
                tuple(path_positions),
            )
        )

    topology = _complete_routing_preserving_fixed_relays(
        state,
        candidate_lowered.lattice.unit_sites,
    )
    candidate_layout = layout_optimizer._materialize_layout(
        base.layout,
        state,
        topology.routing,
    )

    final_fixed = dict(fixed_positions)
    final_fixed.update(
        {relay_id: state.relay_positions[relay_id] for relay_id in reserved_fixed_ids}
    )
    final_base = replace(
        base,
        layout=candidate_layout,
        fixed_positions=final_fixed,
    )
    final_component_problem = replace(
        component_problem,
        layout_problem=final_base,
    )
    final_problem = replace(problem, component_problem=final_component_problem)
    reservation_tuple = tuple(reservations)
    validate_anchored_interface_routing(final_problem, reservation_tuple)
    after = physical_layout_metrics(candidate_layout)
    return AnchoredInterfaceRoutingResult(
        final_problem,
        True,
        None,
        reservation_tuple,
        before,
        after,
        workspace_sites_considered,
    )


def _resolve_interface(
    problem: ComponentLayoutOptimizationProblem,
    state: exact._JointState,
    interface: PublicPortAnchorConstraint,
) -> _ResolvedInterface:
    port = _resolve_public_port(problem.layout_problem.layout.circuit, interface)
    marker = port.marker_entity
    marker_entity = problem.layout_problem.layout.circuit.entity_by_id(marker)
    if not isinstance(marker_entity, ConstantCombinator) or not marker_entity.annotation_only:
        raise ValueError(f"public port {interface.port!r} does not resolve to an annotation marker")

    group = _marker_group(state, marker)
    component = _component_by_name(problem, interface.component)
    access_positions = component.access_positions()
    if interface.access_point not in access_positions:
        raise ValueError(
            f"component {component.name!r} has no access point {interface.access_point!r}"
        )
    access_position = access_positions[interface.access_point]
    group_members = {endpoint.entity for endpoint in state.endpoints_by_group[group]}
    landing_candidates = sorted(component.member_ids & group_members)
    if not landing_candidates:
        raise ValueError(
            f"public port {interface.port!r} electrical group does not enter component "
            f"{component.name!r}"
        )
    positions = problem.layout_problem.layout.positions
    landing = min(
        landing_candidates,
        key=lambda entity_id: (_distance(positions[entity_id], access_position), entity_id),
    )
    landing_position = positions[landing]
    if _distance(landing_position, access_position) > state.safe_span + _EPSILON:
        raise ValueError(
            f"component access point {interface.access_point!r} is not within wire reach of a "
            f"terminal on public port {interface.port!r}"
        )
    return _ResolvedInterface(
        interface,
        marker,
        group,
        component,
        access_position,
        landing,
        landing_position,
    )


def _resolve_public_port(
    circuit: PhysicalCircuit,
    interface: PublicPortAnchorConstraint,
) -> InputPort | OutputPort:
    source_ports = circuit.inputs if interface.direction == "input" else circuit.outputs
    candidates: list[InputPort | OutputPort] = [*source_ports]
    matches = [port for port in candidates if port.name == interface.port]
    if len(matches) != 1:
        raise ValueError(
            f"public {interface.direction} port {interface.port!r} resolved to {len(matches)} ports"
        )
    return matches[0]


def _component_by_name(
    problem: ComponentLayoutOptimizationProblem,
    name: str,
) -> RigidComponentConstraint:
    matches = [component for component in problem.components if component.name == name]
    if len(matches) != 1:
        raise ValueError(f"rigid component {name!r} resolved to {len(matches)} components")
    return matches[0]


def _marker_group(state: exact._JointState, marker_entity: int) -> int:
    groups = [
        group
        for group, endpoints in state.endpoints_by_group.items()
        if any(endpoint.entity == marker_entity for endpoint in endpoints)
    ]
    if len(groups) != 1:
        raise ValueError(
            f"public marker {marker_entity} must belong to exactly one electrical group; "
            f"got {groups}"
        )
    return groups[0]


def _component_exclusion_regions(
    problem: ComponentLayoutOptimizationProblem,
) -> tuple[ComponentRegion, ...]:
    return tuple(
        region
        for component in problem.components
        for region in (
            *component.absolute_footprints(),
            *component.absolute_keepouts(),
            *component.absolute_adapter_regions(),
        )
    )


def _interface_workspace_sites(
    anchor: Position,
    access: Position,
    margin: int,
) -> set[Position]:
    """Return same-phase unit sites in two deterministic dogleg routing corridors."""

    min_dx = floor(min(0.0, access[0] - anchor[0])) - margin
    max_dx = ceil(max(0.0, access[0] - anchor[0])) + margin
    min_dy = floor(min(0.0, access[1] - anchor[1])) - margin
    max_dy = ceil(max(0.0, access[1] - anchor[1])) + margin
    candidates: set[Position] = set()
    first_corner = (access[0], anchor[1])
    second_corner = (anchor[0], access[1])
    segments = (
        (anchor, first_corner),
        (first_corner, access),
        (anchor, second_corner),
        (second_corner, access),
    )
    corridor = float(max(1, margin))
    for dx in range(min_dx, max_dx + 1):
        for dy in range(min_dy, max_dy + 1):
            site = (anchor[0] + dx, anchor[1] + dy)
            if min(_distance_to_axis_segment(site, left, right) for left, right in segments) <= (
                corridor + _EPSILON
            ):
                candidates.add(site)
    return candidates


def _distance_to_axis_segment(point: Position, left: Position, right: Position) -> float:
    if abs(left[0] - right[0]) <= _EPSILON:
        y = min(max(point[1], min(left[1], right[1])), max(left[1], right[1]))
        return _distance(point, (left[0], y))
    if abs(left[1] - right[1]) <= _EPSILON:
        x = min(max(point[0], min(left[0], right[0])), max(left[0], right[0]))
        return _distance(point, (x, left[1]))
    raise AssertionError("interface dogleg segments must be axis-aligned")


def _find_interface_relay_chain(
    start: Position,
    goal: Position,
    free_sites: set[Position],
    safe_span: float,
) -> tuple[Position, ...] | None:
    """Route one reservation without borrowing unrelated terminals on the same net."""

    if _distance(start, goal) <= safe_span + _EPSILON:
        return ()
    workspace = incremental._RelayWorkspace.build(free_sites, safe_span)
    infinity = (10**18, float("inf"))
    costs: dict[Position, tuple[int, float]] = {start: (0, 0.0)}
    previous: dict[Position, Position] = {}
    queue: list[tuple[int, float, int, float, float, float, Position]] = []

    def push(position: Position, cost: tuple[int, float]) -> None:
        distance_to_goal = _distance(position, goal)
        relay_hint = max(0, ceil(distance_to_goal / safe_span - 1e-12) - 1)
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
        _hint_relays, _hint_length, relay_cost, length, _x, _y, position = heappop(queue)
        if costs.get(position, infinity) != (relay_cost, length):
            continue
        if position == goal:
            break

        neighbors = {
            candidate
            for candidate in workspace.nearby_sites(position)
            if candidate != position and _distance(position, candidate) <= safe_span + _EPSILON
        }
        if _distance(position, goal) <= safe_span + _EPSILON:
            neighbors.add(goal)
        for neighbor in sorted(neighbors):
            edge_length = _distance(position, neighbor)
            candidate_cost = (
                relay_cost + (0 if neighbor == goal else 1),
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
        if cursor != goal:
            path.append(cursor)
        parent = previous.get(cursor)
        if parent is None:
            return None
        cursor = parent
    path.reverse()
    return tuple(path)


def _site_overlaps_regions(site: Position, regions: tuple[ComponentRegion, ...]) -> bool:
    return any(region.overlaps_box(site, _RELAY_HALF_EXTENT) for region in regions)


def _site_overlaps_forbidden(
    site: Position,
    forbidden_areas: tuple[RelayForbiddenArea, ...],
) -> bool:
    x, y = site
    half_x, half_y = _RELAY_HALF_EXTENT
    return any(
        x + half_x > left + _EPSILON
        and x - half_x < right - _EPSILON
        and y + half_y > top + _EPSILON
        and y - half_y < bottom - _EPSILON
        for left, right, top, bottom in forbidden_areas
    )


def _complete_routing_preserving_fixed_relays(
    state: exact._JointState,
    legal_unit_sites: tuple[Position, ...],
) -> incremental._FeasibleTopology:
    """Fresh-route every remaining group while retaining pre-reserved interface relays."""

    fixed_relays = {
        relay_id: position
        for relay_id, position in state.relay_positions.items()
        if relay_id in state.fixed_objects
    }
    fixed_groups = {relay_id: state.relay_groups[relay_id] for relay_id in fixed_relays}
    occupancy = incremental._SpatialOccupancy.build(state)
    base_free_sites = {
        site
        for site in legal_unit_sites
        if not incremental._box_overlaps_occupancy(
            occupancy,
            site,
            _RELAY_HALF_EXTENT,
            ignored=set(),
        )
        and not _site_overlaps_forbidden(site, state.forbidden_areas)
    }
    orders = _routing_group_orders(state)
    last_failure = "unknown anchored routing failure"

    for order in orders:
        state.relay_positions.clear()
        state.relay_positions.update(fixed_relays)
        state.relay_groups.clear()
        state.relay_groups.update(fixed_groups)
        free_sites = set(base_free_sites)
        workspace = incremental._RelayWorkspace.build(free_sites, state.safe_span)
        next_relay_id = (
            max([entity.id for entity in state.circuit.entities] + list(fixed_relays) + [0]) + 1
        )
        failed = False

        for group in order:
            attempts = 0
            while exact._group_spanning_tree(state, group) is None:
                attempts += 1
                if attempts > len(free_sites) + 1:
                    last_failure = f"physical net {group} routing did not converge"
                    failed = True
                    break
                components = _group_reach_components(state, group)
                if len(components) <= 1:
                    break
                left, right = _nearest_component_vertices(state, components)
                chain = incremental._find_relay_chain(
                    state,
                    group,
                    left[1],
                    right[1],
                    free_sites,
                    workspace,
                )
                if not chain:
                    last_failure = (
                        f"physical net {group} cannot connect anchored reach components "
                        f"{left!r}/{right!r}"
                    )
                    failed = True
                    break
                for site in chain:
                    relay_id = next_relay_id
                    next_relay_id += 1
                    state.relay_positions[relay_id] = site
                    state.relay_groups[relay_id] = frozenset({group})
                    free_sites.discard(site)
            if failed:
                break

        if failed:
            continue
        if any(
            exact._group_spanning_tree(state, group) is None for group in state.endpoints_by_group
        ):
            last_failure = "fresh anchored routing left at least one physical net disconnected"
            continue

        routing = exact._routing_plan(state)
        topology = incremental._FeasibleTopology.build(state, routing)
        while True:
            relay_count = len(state.relay_positions)
            topology = incremental._simplify_feasible_topology(state, topology)
            if len(state.relay_positions) == relay_count:
                break
        return topology

    state.relay_positions.clear()
    state.relay_positions.update(fixed_relays)
    state.relay_groups.clear()
    state.relay_groups.update(fixed_groups)
    raise ValueError(last_failure)


def _routing_group_orders(state: exact._JointState) -> tuple[tuple[int, ...], ...]:
    scored: list[tuple[float, int]] = []
    for group, endpoints in state.endpoints_by_group.items():
        terminals = tuple(exact._terminal_vertex(endpoint) for endpoint in endpoints)
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
        scored.append((-overreach, group))
    hardest = tuple(group for _score, group in sorted(scored))
    by_id = tuple(sorted(state.endpoints_by_group))
    candidates = (hardest, by_id, tuple(reversed(hardest)))
    result: list[tuple[int, ...]] = []
    for candidate in candidates:
        if candidate not in result:
            result.append(candidate)
    return tuple(result)


def _group_reach_components(
    state: exact._JointState,
    group: int,
) -> tuple[tuple[exact.Vertex, ...], ...]:
    remaining = set(state.group_vertices(group))
    components: list[tuple[exact.Vertex, ...]] = []
    while remaining:
        root = min(remaining)
        remaining.remove(root)
        component = {root}
        frontier = [root]
        while frontier:
            left = frontier.pop()
            left_position = state.vertex_position(left)
            neighbors = [
                right
                for right in sorted(remaining)
                if _distance(left_position, state.vertex_position(right))
                <= state.safe_span + _EPSILON
            ]
            for right in neighbors:
                remaining.remove(right)
                component.add(right)
                frontier.append(right)
        components.append(tuple(sorted(component)))
    return tuple(components)


def _nearest_component_vertices(
    state: exact._JointState,
    components: tuple[tuple[exact.Vertex, ...], ...],
) -> tuple[exact.Vertex, exact.Vertex]:
    candidates: list[tuple[float, exact.Vertex, exact.Vertex]] = []
    for left_index, left_component in enumerate(components):
        for right_component in components[left_index + 1 :]:
            candidates.extend(
                (
                    _distance(state.vertex_position(left), state.vertex_position(right)),
                    left,
                    right,
                )
                for left in left_component
                for right in right_component
            )
    if not candidates:
        raise ValueError("cannot choose vertices from fewer than two reach components")
    _distance_value, left, right = min(candidates)
    return left, right


def _distance(left: Position, right: Position) -> float:
    return hypot(left[0] - right[0], left[1] - right[1])


__all__ = [
    "AnchoredInterfaceLayoutProblem",
    "AnchoredInterfaceRoutingResult",
    "AnchoredRelayReservation",
    "PublicPortAnchorConstraint",
    "route_anchored_interfaces_transactionally",
    "validate_anchored_interface_routing",
]
