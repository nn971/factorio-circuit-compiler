"""Constructive, search-free physical layout for supported Factorio circuits.

The safe crossbar deliberately spends area and relay entities to make materialization predictable.
All implementation entities are placed on one sparse horizontal row. Physical electrical groups use
horizontal bus segments above (RED) or below (GREEN) that row, and every endpoint reaches its bus
through a unique vertical feeder column.

Bus rows are reusable: two same-color groups share a row whenever their horizontal relay intervals,
including collision clearance, are disjoint. Deterministic interval partitioning therefore uses the
minimum number of tracks for the fixed entity order while preserving the no-search correctness proof.
The reusable tracks are only two tiles apart; the six-tile pitch is reserved for relay hops along wires.

Implementation entities sit at ``x = 0 (mod 6)``. INPUT/SINGLE feeders use ``x = -2 (mod 6)`` and
OUTPUT feeders use ``x = +2 (mod 6)``. Ordinary feeder relays use ``y = 0 (mod 6)`` away from the
entity row, while bus rows are odd integer y coordinates starting at +/-3. Ordinary bus relays remain
at ``x = 0 (mod 6)``; only the owning endpoint inserts a tap at its feeder column. Unrelated
feeder/bus crossings therefore contain no relay.

This policy does not call the normal placement optimizer or collision-avoiding wire router. It is a
correctness fallback, not a compactness strategy.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from heapq import heappop, heappush
from math import ceil, floor, sqrt
from typing import Any, cast

from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import (
    ArithmeticCombinator,
    Connector,
    ConstantCombinator,
    DeciderCombinator,
    PhysicalCircuit,
    PhysicalEntity,
    SignalId,
    WireColor,
    WireConnection,
    WireEndpoint,
)
from factorio_circuit.progress import ProgressCallback, report_progress
from factorio_circuit.synthesis.layout import Layout, LayoutRelay, LayoutWire
from factorio_circuit.synthesis.placement import PlacementOptions

_SAFE_PITCH = 6.0
_ENTITY_SPACING = 6.0
_FEEDER_OFFSET = 2.0
_FIRST_BUS_OFFSET = 3.0
_TRACK_SPACING = 2.0
_RELAY_CENTER_CLEARANCE = 1.1
DEFAULT_SAFE_CROSSBAR_MAX_RELAYS = 1_000_000
# Endpoint -> first regular feeder relay is sqrt(2^2 + 6^2).
_MINIMUM_SAFE_SPAN = sqrt(_FEEDER_OFFSET**2 + _SAFE_PITCH**2)


@dataclass(frozen=True, slots=True)
class SafeCrossbarPreflight:
    """Exact size diagnostics for the constructive layout before relays are allocated."""

    physical_groups: int
    routed_groups: int
    singleton_groups: int
    red_tracks: int
    green_tracks: int
    predicted_relays: int


@dataclass(frozen=True, slots=True)
class _GroupRoute:
    group: int
    color: WireColor
    endpoints: tuple[abstract.Endpoint, ...]
    min_x: float
    max_x: float
    track: int
    bus_y: float


@dataclass(frozen=True, slots=True)
class _CrossbarPlan:
    routes: dict[int, _GroupRoute]
    preflight: SafeCrossbarPreflight


def safe_crossbar_options() -> PlacementOptions:
    """Return options selecting the joint safe-crossbar synthesis policy.

    ``PlacementStrategy`` still names only the older optimizer-specific strategies. Keeping the cast
    here localizes that temporary typing gap until the next physical-synthesis API cleanup.
    """

    return PlacementOptions(strategy=cast(Any, "safe-crossbar"), restarts=1)


def build_safe_crossbar_layout(
    abstract_circuit: abstract.AbstractPhysicalCircuit,
    physical: PhysicalCircuit,
    *,
    net_colors: dict[int, WireColor],
    net_groups: dict[int, int],
    signal_allocation: dict[int, SignalId],
    safe_wire_span: float,
    progress: ProgressCallback | None = None,
    max_relays: int | None = DEFAULT_SAFE_CROSSBAR_MAX_RELAYS,
) -> Layout:
    """Materialize ``physical`` using deterministic reusable bus/feeder geometry.

    The supported subset currently assumes the compiler's ordinary horizontal combinators and blank
    constant-combinator relays, no user-specified fixed entity coordinates, and a conservative wire
    span large enough for the six-tile construction. Within that subset there is no geometric search
    or retry path: every relay coordinate follows directly from entity, endpoint, and physical-net
    order.

    ``max_relays`` is a safety guard evaluated from an exact preflight count before relay objects are
    created. Pass ``None`` only when intentionally requesting an arbitrarily large fallback layout.
    """

    if safe_wire_span + 1e-9 < _MINIMUM_SAFE_SPAN:
        raise ValueError(
            "safe-crossbar requires blueprint_safe_wire_span >= "
            f"{_MINIMUM_SAFE_SPAN:.3f}; got {safe_wire_span:.3f}"
        )
    if max_relays is not None and max_relays < 0:
        raise ValueError("safe-crossbar max_relays must be nonnegative or None")

    ordered_entities = _ordered_entities(physical)
    positions = {
        entity.id: (float(index) * _ENTITY_SPACING, 0.0)
        for index, entity in enumerate(ordered_entities)
    }

    endpoints_by_group: dict[int, set[abstract.Endpoint]] = defaultdict(set)
    colors_by_group: dict[int, WireColor] = {}
    for net in abstract_circuit.nets:
        group = net_groups[net.id]
        endpoints_by_group[group].update(net.endpoints)
        color = net_colors[net.id]
        previous = colors_by_group.setdefault(group, color)
        if previous != color:  # pragma: no cover - grouping invariant
            raise AssertionError(f"physical net group {group} contains multiple wire colors")

    plan = _plan_crossbar(endpoints_by_group, colors_by_group, positions)
    stats = plan.preflight
    report_progress(
        progress,
        "safe-layout",
        detail=(
            f"preflight: groups={stats.physical_groups}; routed={stats.routed_groups}; "
            f"singletons={stats.singleton_groups}; tracks=red:{stats.red_tracks},"
            f"green:{stats.green_tracks}; predicted_relays={stats.predicted_relays}"
        ),
    )
    if max_relays is not None and stats.predicted_relays > max_relays:
        raise ValueError(
            "safe-crossbar preflight refused a pathological fallback layout: "
            f"predicted {stats.predicted_relays} relays exceeds safety cap {max_relays}; "
            f"physical_groups={stats.physical_groups}, routed_groups={stats.routed_groups}, "
            f"red_tracks={stats.red_tracks}, green_tracks={stats.green_tracks}. "
            "Use optimized routing or explicitly call build_safe_crossbar_layout(..., "
            "max_relays=None) only if such a large blueprint is intentional."
        )

    next_entity_id = max((entity.id for entity in physical.entities), default=0) + 1
    relays: list[LayoutRelay] = []
    wires: list[LayoutWire] = []
    relay_positions: dict[tuple[float, float], tuple[int, int]] = {}
    wire_keys: set[tuple[int, int, int, int, WireColor]] = set()

    def add_relay(position: tuple[float, float], *, group: int, role: str) -> int:
        nonlocal next_entity_id
        if abs(position[1]) < 1e-9:
            raise AssertionError("safe-crossbar relay intruded into the implementation entity row")
        previous = relay_positions.get(position)
        if previous is not None:
            previous_id, previous_group = previous
            if previous_group != group:
                raise AssertionError(
                    "safe-crossbar formula assigned one relay site to distinct physical groups: "
                    f"{position} -> {previous_group}, {group}"
                )
            return previous_id
        relay_id = next_entity_id
        next_entity_id += 1
        relay_positions[position] = (relay_id, group)
        relays.append(
            LayoutRelay(
                relay_id,
                position,
                f"SAFE CROSSBAR {role} — physical net {group}",
            )
        )
        return relay_id

    def add_wire(
        left_entity: int,
        left_connector: int,
        right_entity: int,
        right_connector: int,
        color: WireColor,
    ) -> None:
        if left_entity == right_entity and left_connector == right_connector:
            return
        if (left_entity, left_connector) <= (right_entity, right_connector):
            key = (left_entity, left_connector, right_entity, right_connector, color)
        else:
            key = (right_entity, right_connector, left_entity, left_connector, color)
        if key in wire_keys:
            return
        wire_keys.add(key)
        wires.append(LayoutWire(left_entity, left_connector, right_entity, right_connector, color))

    # Keep the relay-free logical PhysicalCircuit useful for physical simulation. Geometry belongs
    # to Layout; the direct connections below merely encode the same electrical components.
    physical.connections.clear()
    total_routes = len(plan.routes)
    completed_routes = 0
    report_progress(
        progress,
        "safe-layout",
        completed=0,
        total=total_routes,
        detail="constructing interval-packed physical-net buses",
    )

    for group in sorted(plan.routes):
        route = plan.routes[group]
        endpoints = route.endpoints
        color = route.color
        for left, right in zip(endpoints, endpoints[1:], strict=False):
            physical.connections.append(
                WireConnection(_wire_endpoint(left), _wire_endpoint(right), color)
            )

        tap_nodes: list[tuple[float, int]] = []
        for endpoint in endpoints:
            entity_x, _entity_y = positions[endpoint.entity]
            feeder_x = entity_x + _feeder_offset(endpoint.connector)
            tap_position = (feeder_x, route.bus_y)
            tap_id = add_relay(tap_position, group=group, role=f"{color.value} tap")
            tap_nodes.append((feeder_x, tap_id))

            feeder_nodes: list[int] = []
            sign = -1 if route.bus_y < 0 else 1
            y = sign * _SAFE_PITCH
            while y > route.bus_y if sign < 0 else y < route.bus_y:
                feeder_nodes.append(
                    add_relay(
                        (feeder_x, y),
                        group=group,
                        role=f"{color.value} feeder",
                    )
                )
                y += sign * _SAFE_PITCH
            feeder_nodes.append(tap_id)

            real_connector = _real_connector_id(physical, endpoint, color)
            relay_connector = _relay_connector_id(color)
            first = feeder_nodes[0]
            add_wire(endpoint.entity, real_connector, first, relay_connector, color)
            for left_id, right_id in zip(feeder_nodes, feeder_nodes[1:], strict=False):
                add_wire(left_id, relay_connector, right_id, relay_connector, color)

        trunk_nodes = list(tap_nodes)
        first_regular = ceil(route.min_x / _SAFE_PITCH) * _SAFE_PITCH
        last_regular = floor(route.max_x / _SAFE_PITCH) * _SAFE_PITCH
        x = first_regular
        while x <= last_regular + 1e-9:
            trunk_nodes.append(
                (
                    x,
                    add_relay(
                        (x, route.bus_y),
                        group=group,
                        role=f"{color.value} bus",
                    ),
                )
            )
            x += _SAFE_PITCH

        relay_connector = _relay_connector_id(color)
        trunk_nodes.sort(key=lambda item: (item[0], item[1]))
        for (_left_x, left_id), (_right_x, right_id) in zip(
            trunk_nodes, trunk_nodes[1:], strict=False
        ):
            add_wire(left_id, relay_connector, right_id, relay_connector, color)

        completed_routes += 1
        report_progress(
            progress,
            "safe-layout",
            completed=completed_routes,
            total=total_routes,
            detail=f"relays={len(relays)}; wires={len(wires)}",
        )

    if len(relays) != stats.predicted_relays:
        raise AssertionError(
            "safe-crossbar preflight relay count disagrees with construction: "
            f"predicted {stats.predicted_relays}, emitted {len(relays)}"
        )

    all_positions = dict(positions)
    all_positions.update({relay.entity_id: relay.position for relay in relays})
    _validate_wire_spans(wires, all_positions, maximum_span=safe_wire_span)

    report_progress(
        progress,
        "safe-layout",
        completed=total_routes,
        total=total_routes,
        detail=(
            f"complete; entities={len(physical.entities)}; relays={len(relays)}; "
            f"tracks=red:{stats.red_tracks},green:{stats.green_tracks}"
        ),
    )
    return Layout(
        circuit=physical,
        positions=all_positions,
        relays=tuple(relays),
        wires=tuple(wires),
        signal_allocation=tuple(sorted(signal_allocation.items())),
        net_colors=tuple(sorted(net_colors.items())),
        net_groups=tuple(sorted(net_groups.items())),
    )


def _plan_crossbar(
    endpoints_by_group: dict[int, set[abstract.Endpoint]],
    colors_by_group: dict[int, WireColor],
    positions: dict[int, tuple[float, float]],
) -> _CrossbarPlan:
    routes: dict[int, _GroupRoute] = {}
    track_counts: dict[WireColor, int] = {WireColor.RED: 0, WireColor.GREEN: 0}

    for color, sign in ((WireColor.RED, -1.0), (WireColor.GREEN, 1.0)):
        intervals: list[tuple[float, float, int, tuple[abstract.Endpoint, ...]]] = []
        for group, group_color in colors_by_group.items():
            if group_color != color:
                continue
            endpoints = tuple(sorted(endpoints_by_group[group]))
            if len(endpoints) < 2:
                continue
            feeder_xs = [
                positions[endpoint.entity][0] + _feeder_offset(endpoint.connector)
                for endpoint in endpoints
            ]
            intervals.append((min(feeder_xs), max(feeder_xs), group, endpoints))

        assignments, track_count = _assign_interval_tracks(intervals)
        track_counts[color] = track_count
        for min_x, max_x, group, endpoints in intervals:
            track = assignments[group]
            routes[group] = _GroupRoute(
                group=group,
                color=color,
                endpoints=endpoints,
                min_x=min_x,
                max_x=max_x,
                track=track,
                bus_y=sign * (_FIRST_BUS_OFFSET + track * _TRACK_SPACING),
            )

    predicted_relays = sum(_route_relay_count(route) for route in routes.values())
    physical_groups = len(endpoints_by_group)
    routed_groups = len(routes)
    return _CrossbarPlan(
        routes=routes,
        preflight=SafeCrossbarPreflight(
            physical_groups=physical_groups,
            routed_groups=routed_groups,
            singleton_groups=physical_groups - routed_groups,
            red_tracks=track_counts[WireColor.RED],
            green_tracks=track_counts[WireColor.GREEN],
            predicted_relays=predicted_relays,
        ),
    )


def _assign_interval_tracks(
    intervals: list[tuple[float, float, int, tuple[abstract.Endpoint, ...]]],
) -> tuple[dict[int, int], int]:
    """Assign and endpoint-weight reusable tracks for fixed closed relay intervals."""

    active: list[tuple[float, int]] = []
    available_tracks: list[int] = []
    assignments: dict[int, int] = {}
    next_track = 0

    for min_x, max_x, group, _endpoints in sorted(
        intervals, key=lambda item: (item[0], item[1], item[2])
    ):
        while active and active[0][0] <= min_x + 1e-9:
            _release_x, track = heappop(active)
            heappush(available_tracks, track)

        if available_tracks:
            track = heappop(available_tracks)
        else:
            track = next_track
            next_track += 1
        assignments[group] = track
        heappush(active, (max_x + _RELAY_CENTER_CLEARANCE, track))

    # Track identities are geometrically interchangeable. Reorder whole valid tracks so the tracks
    # carrying the most endpoint feeders sit closest to the entity row. This minimizes the weighted
    # feeder depth for this fixed interval partition without changing any overlap relationship.
    endpoint_weight = {track: 0 for track in range(next_track)}
    for _min_x, _max_x, group, endpoints in intervals:
        endpoint_weight[assignments[group]] += len(endpoints)
    old_tracks = sorted(range(next_track), key=lambda track: (-endpoint_weight[track], track))
    remap = {old_track: new_track for new_track, old_track in enumerate(old_tracks)}
    return {group: remap[track] for group, track in assignments.items()}, next_track


def _route_relay_count(route: _GroupRoute) -> int:
    """Return the exact relay count emitted for one route before allocating any objects."""

    # Vertical feeder relays stay on the six-tile lattice even though bus tracks are only two tiles
    # apart. If the tap itself lies before/on the next six-tile point, no ordinary feeder is needed.
    regular_feeder_relays = max(0, ceil(abs(route.bus_y) / _SAFE_PITCH) - 1)
    feeder_and_taps = len(route.endpoints) * (regular_feeder_relays + 1)
    first_regular = ceil(route.min_x / _SAFE_PITCH)
    last_regular = floor(route.max_x / _SAFE_PITCH)
    bus_relays = max(0, last_regular - first_regular + 1)
    return feeder_and_taps + bus_relays


def _ordered_entities(physical: PhysicalCircuit) -> list[PhysicalEntity]:
    """Keep public input/output markers at the two accessible ends of the sparse row."""

    input_ids = [port.marker_entity for port in physical.inputs]
    output_ids = [port.marker_entity for port in physical.outputs]
    edge_ids = set(input_ids) | set(output_ids)
    body_ids = sorted(entity.id for entity in physical.entities if entity.id not in edge_ids)

    ordered_ids: list[int] = []
    seen: set[int] = set()
    for entity_id in [*input_ids, *body_ids, *output_ids]:
        if entity_id not in seen:
            ordered_ids.append(entity_id)
            seen.add(entity_id)
    return [physical.entity_by_id(entity_id) for entity_id in ordered_ids]


def _feeder_offset(connector: abstract.Connector) -> float:
    if connector is abstract.Connector.OUTPUT:
        return _FEEDER_OFFSET
    if connector in {abstract.Connector.INPUT, abstract.Connector.SINGLE}:
        return -_FEEDER_OFFSET
    raise TypeError(connector)


def _wire_endpoint(endpoint: abstract.Endpoint) -> WireEndpoint:
    return WireEndpoint(endpoint.entity, Connector(endpoint.connector.value))


def _real_connector_id(
    circuit: PhysicalCircuit,
    endpoint: abstract.Endpoint,
    color: WireColor,
) -> int:
    entity = circuit.entity_by_id(endpoint.entity)
    if isinstance(entity, ConstantCombinator):
        if endpoint.connector is not abstract.Connector.SINGLE:
            raise ValueError("constant combinator only has a single circuit connector")
        base = 1
    elif isinstance(entity, (ArithmeticCombinator, DeciderCombinator)):
        if endpoint.connector is abstract.Connector.INPUT:
            base = 1
        elif endpoint.connector is abstract.Connector.OUTPUT:
            base = 3
        else:
            raise ValueError("arithmetic/decider endpoint must be INPUT or OUTPUT")
    else:  # pragma: no cover - current physical subset is closed
        raise TypeError(entity)
    return base if color is WireColor.RED else base + 1


def _relay_connector_id(color: WireColor) -> int:
    return 1 if color is WireColor.RED else 2


def _validate_wire_spans(
    wires: list[LayoutWire],
    positions: dict[int, tuple[float, float]],
    *,
    maximum_span: float,
) -> None:
    """Linear postcondition check for the constructive geometry."""

    for wire in wires:
        left = positions[wire.source_entity]
        right = positions[wire.target_entity]
        distance = sqrt((left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2)
        if distance > maximum_span + 1e-9:
            raise AssertionError(
                "safe-crossbar construction emitted an overlong wire: "
                f"{wire.source_entity}->{wire.target_entity} spans {distance:.3f} > "
                f"{maximum_span:.3f}"
            )
