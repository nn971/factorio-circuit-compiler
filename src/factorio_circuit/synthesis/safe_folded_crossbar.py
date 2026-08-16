"""Fold the proven linear safe crossbar into a bounded serpentine fabric.

This module is intentionally separate from :mod:`safe_crossbar`. The existing linear
``safe-crossbar`` remains the simple reference/fallback. ``safe-folded-crossbar`` keeps the same
one-dimensional entity order and interval-track assignment, then folds that order into deterministic
serpentine rows. A physical net that crosses a fold receives a vertical stitch on a track-specific
portal column. No placement search, routing search, retries, or backtracking are used.

The construction preserves the linear ordering proof:

* same-color groups may share a track only when their linear feeder intervals are disjoint;
* folding is monotone inside each row (alternating orientation), so disjoint intervals stay disjoint;
* a group crossing a row boundary is the only group on that track whose interval contains that
  boundary;
* ordinary horizontal relays use ``x = 0 (mod 6)`` while portal columns are odd x coordinates;
* ordinary vertical relays use ``y = 0 (mod 6)`` while bus rows are odd offsets from entity rows.

Public input and output marker entities are deliberately placed first in the folded order, producing a
compact I/O front panel near the first row instead of separating outputs at the far end of the module.
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
_ROW_MARGIN = 12.0
_PORTAL_GAP = 6.0
_PORTAL_FIRST_OFFSET = 3.0
_PORTAL_SPACING = 2.0
DEFAULT_SAFE_FOLDED_MAX_RELAYS = 1_000_000
DEFAULT_SAFE_FOLDED_MAX_EXTENT = 4096.0
_MINIMUM_SAFE_SPAN = sqrt(_FEEDER_OFFSET**2 + _SAFE_PITCH**2)


@dataclass(frozen=True, slots=True)
class SafeFoldedPreflight:
    """Exact/countable diagnostics computed before relay objects are allocated."""

    physical_groups: int
    routed_groups: int
    singleton_groups: int
    red_tracks: int
    green_tracks: int
    entity_rows: int
    entities_per_row: int
    predicted_relays: int
    predicted_width: float
    predicted_height: float


@dataclass(frozen=True, slots=True)
class _BaseRoute:
    group: int
    color: WireColor
    endpoints: tuple[abstract.Endpoint, ...]
    track: int
    start_row: int
    end_row: int


@dataclass(frozen=True, slots=True)
class _FoldedPlan:
    ordered_entities: tuple[PhysicalEntity, ...]
    positions: dict[int, tuple[float, float]]
    entity_index: dict[int, int]
    routes: dict[int, _BaseRoute]
    red_tracks: int
    green_tracks: int
    row_pitch: float
    entities_per_row: int
    entity_rows: int
    preflight: SafeFoldedPreflight


def safe_folded_crossbar_options() -> PlacementOptions:
    """Return options selecting the folded constructive synthesis policy."""

    return PlacementOptions(strategy=cast(Any, "safe-folded-crossbar"), restarts=1)


def build_safe_folded_crossbar_layout(
    abstract_circuit: abstract.AbstractPhysicalCircuit,
    physical: PhysicalCircuit,
    *,
    net_colors: dict[int, WireColor],
    net_groups: dict[int, int],
    signal_allocation: dict[int, SignalId],
    safe_wire_span: float,
    progress: ProgressCallback | None = None,
    max_relays: int | None = DEFAULT_SAFE_FOLDED_MAX_RELAYS,
    max_extent: float | None = DEFAULT_SAFE_FOLDED_MAX_EXTENT,
) -> Layout:
    """Materialize a deterministic folded crossbar without geometric search.

    The old linear ``safe-crossbar`` implementation is intentionally untouched and remains available
    as a rollback/reference strategy. ``max_relays`` and ``max_extent`` are preflight guards: they are
    checked before any relay objects are created.
    """

    if safe_wire_span + 1e-9 < _MINIMUM_SAFE_SPAN:
        raise ValueError(
            "safe-folded-crossbar requires blueprint_safe_wire_span >= "
            f"{_MINIMUM_SAFE_SPAN:.3f}; got {safe_wire_span:.3f}"
        )
    if max_relays is not None and max_relays < 0:
        raise ValueError("safe-folded-crossbar max_relays must be nonnegative or None")
    if max_extent is not None and max_extent <= 0:
        raise ValueError("safe-folded-crossbar max_extent must be positive or None")

    endpoints_by_group, colors_by_group = _group_endpoints(
        abstract_circuit,
        net_colors=net_colors,
        net_groups=net_groups,
    )
    plan = _plan_folded_crossbar(physical, endpoints_by_group, colors_by_group)
    stats = plan.preflight
    report_progress(
        progress,
        "safe-folded-layout",
        detail=(
            f"preflight: groups={stats.physical_groups}; routed={stats.routed_groups}; "
            f"singletons={stats.singleton_groups}; tracks=red:{stats.red_tracks},"
            f"green:{stats.green_tracks}; rows={stats.entity_rows}; "
            f"columns={stats.entities_per_row}; predicted_relays={stats.predicted_relays}; "
            f"extent={stats.predicted_width:.0f}x{stats.predicted_height:.0f}"
        ),
    )
    if max_relays is not None and stats.predicted_relays > max_relays:
        raise ValueError(
            "safe-folded-crossbar preflight refused a pathological fallback layout: "
            f"predicted {stats.predicted_relays} relays exceeds safety cap {max_relays}"
        )
    if max_extent is not None and max(stats.predicted_width, stats.predicted_height) > max_extent:
        raise ValueError(
            "safe-folded-crossbar preflight refused an impractically large footprint: "
            f"predicted {stats.predicted_width:.0f}x{stats.predicted_height:.0f} tiles exceeds "
            f"maximum extent {max_extent:.0f}; use the linear safe-crossbar for the canonical "
            "fallback or revise folded geometry"
        )

    next_entity_id = max((entity.id for entity in physical.entities), default=0) + 1
    relays: list[LayoutRelay] = []
    wires: list[LayoutWire] = []
    relay_positions: dict[tuple[float, float], tuple[int, int]] = {}
    wire_keys: set[tuple[int, int, int, int, WireColor]] = set()

    def add_relay(position: tuple[float, float], *, group: int, role: str) -> int:
        nonlocal next_entity_id
        previous = relay_positions.get(position)
        if previous is not None:
            previous_id, previous_group = previous
            if previous_group != group:
                raise AssertionError(
                    "safe-folded-crossbar formula assigned one relay site to distinct groups: "
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
                f"SAFE FOLDED {role} — physical net {group}",
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

    physical.connections.clear()
    total_routes = len(plan.routes)
    report_progress(
        progress,
        "safe-folded-layout",
        completed=0,
        total=total_routes,
        detail="constructing serpentine bus segments and fold stitches",
    )

    for completed_routes, group in enumerate(sorted(plan.routes), start=1):
        route = plan.routes[group]
        endpoints = route.endpoints
        color = route.color
        relay_connector = _relay_connector_id(color)

        for left, right in zip(endpoints, endpoints[1:], strict=False):
            physical.connections.append(
                WireConnection(_wire_endpoint(left), _wire_endpoint(right), color)
            )

        endpoints_by_row: dict[int, list[abstract.Endpoint]] = defaultdict(list)
        for endpoint in endpoints:
            endpoints_by_row[_entity_row(plan, endpoint.entity)].append(endpoint)

        # Endpoint feeders and row bus segments. A middle row with no real endpoint still carries a
        # segment between its incoming and outgoing fold portals when the net spans that row.
        for row in range(route.start_row, route.end_row + 1):
            row_y = row * plan.row_pitch
            bus_y = _bus_y(row_y, color, route.track)
            bus_nodes: list[tuple[float, int]] = []

            for endpoint in endpoints_by_row.get(row, []):
                entity_x, entity_y = plan.positions[endpoint.entity]
                feeder_x = entity_x + _feeder_offset(endpoint.connector)
                tap_id = add_relay(
                    (feeder_x, bus_y),
                    group=group,
                    role=f"{color.value} endpoint tap",
                )
                bus_nodes.append((feeder_x, tap_id))

                feeder_nodes = _vertical_feeder_nodes(
                    feeder_x,
                    entity_y,
                    bus_y,
                    group=group,
                    add_relay=add_relay,
                    role=f"{color.value} feeder",
                )
                first = feeder_nodes[0]
                add_wire(
                    endpoint.entity,
                    _real_connector_id(physical, endpoint, color),
                    first,
                    relay_connector,
                    color,
                )
                for left_id, right_id in zip(feeder_nodes, feeder_nodes[1:], strict=False):
                    add_wire(left_id, relay_connector, right_id, relay_connector, color)

            if row > route.start_row:
                portal_x = _portal_x(plan, row - 1, color, route.track)
                portal_id = add_relay(
                    (portal_x, bus_y),
                    group=group,
                    role=f"{color.value} fold tap",
                )
                bus_nodes.append((portal_x, portal_id))
            if row < route.end_row:
                portal_x = _portal_x(plan, row, color, route.track)
                portal_id = add_relay(
                    (portal_x, bus_y),
                    group=group,
                    role=f"{color.value} fold tap",
                )
                bus_nodes.append((portal_x, portal_id))

            if len(bus_nodes) < 2:
                raise AssertionError(
                    f"folded route {group} row {row} has fewer than two bus attachment points"
                )
            _connect_horizontal_segment(
                bus_nodes,
                bus_y,
                group=group,
                color=color,
                relay_connector=relay_connector,
                add_relay=add_relay,
                add_wire=add_wire,
            )

        # Stitch each crossed fold at one deterministic, track-specific portal column.
        for boundary in range(route.start_row, route.end_row):
            portal_x = _portal_x(plan, boundary, color, route.track)
            upper_row_y = boundary * plan.row_pitch
            lower_row_y = (boundary + 1) * plan.row_pitch
            upper_bus_y = _bus_y(upper_row_y, color, route.track)
            lower_bus_y = _bus_y(lower_row_y, color, route.track)
            top_tap = add_relay(
                (portal_x, upper_bus_y),
                group=group,
                role=f"{color.value} fold tap",
            )
            bottom_tap = add_relay(
                (portal_x, lower_bus_y),
                group=group,
                role=f"{color.value} fold tap",
            )
            stitch_nodes: list[tuple[float, int]] = [(upper_bus_y, top_tap)]
            first_regular = ceil(upper_bus_y / _SAFE_PITCH) * _SAFE_PITCH
            if first_regular <= upper_bus_y + 1e-9:
                first_regular += _SAFE_PITCH
            y = first_regular
            while y < lower_bus_y - 1e-9:
                stitch_nodes.append(
                    (
                        y,
                        add_relay(
                            (portal_x, y),
                            group=group,
                            role=f"{color.value} fold stitch",
                        ),
                    )
                )
                y += _SAFE_PITCH
            stitch_nodes.append((lower_bus_y, bottom_tap))
            stitch_nodes.sort(key=lambda item: (item[0], item[1]))
            for (_left_y, left_id), (_right_y, right_id) in zip(
                stitch_nodes,
                stitch_nodes[1:],
                strict=False,
            ):
                add_wire(left_id, relay_connector, right_id, relay_connector, color)

        report_progress(
            progress,
            "safe-folded-layout",
            completed=completed_routes,
            total=total_routes,
            detail=f"relays={len(relays)}; wires={len(wires)}",
        )

    if len(relays) != stats.predicted_relays:
        raise AssertionError(
            "safe-folded-crossbar preflight relay count disagrees with construction: "
            f"predicted {stats.predicted_relays}, emitted {len(relays)}"
        )

    all_positions = dict(plan.positions)
    all_positions.update({relay.entity_id: relay.position for relay in relays})
    _validate_wire_spans(wires, all_positions, maximum_span=safe_wire_span)
    report_progress(
        progress,
        "safe-folded-layout",
        completed=total_routes,
        total=total_routes,
        detail=(
            f"complete; entities={len(physical.entities)}; relays={len(relays)}; "
            f"rows={stats.entity_rows}; columns={stats.entities_per_row}; "
            f"extent={stats.predicted_width:.0f}x{stats.predicted_height:.0f}"
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


def _group_endpoints(
    abstract_circuit: abstract.AbstractPhysicalCircuit,
    *,
    net_colors: dict[int, WireColor],
    net_groups: dict[int, int],
) -> tuple[dict[int, set[abstract.Endpoint]], dict[int, WireColor]]:
    endpoints_by_group: dict[int, set[abstract.Endpoint]] = defaultdict(set)
    colors_by_group: dict[int, WireColor] = {}
    for net in abstract_circuit.nets:
        group = net_groups[net.id]
        endpoints_by_group[group].update(net.endpoints)
        color = net_colors[net.id]
        previous = colors_by_group.setdefault(group, color)
        if previous != color:  # pragma: no cover - synthesis grouping invariant
            raise AssertionError(f"physical net group {group} contains multiple wire colors")
    return endpoints_by_group, colors_by_group


def _plan_folded_crossbar(
    physical: PhysicalCircuit,
    endpoints_by_group: dict[int, set[abstract.Endpoint]],
    colors_by_group: dict[int, WireColor],
) -> _FoldedPlan:
    ordered_entities = tuple(_folded_ordered_entities(physical))
    entity_index = {entity.id: index for index, entity in enumerate(ordered_entities)}
    virtual_x = {
        entity.id: index * _ENTITY_SPACING for index, entity in enumerate(ordered_entities)
    }

    route_specs: dict[int, tuple[WireColor, tuple[abstract.Endpoint, ...], int]] = {}
    track_counts: dict[WireColor, int] = {WireColor.RED: 0, WireColor.GREEN: 0}
    for color in (WireColor.RED, WireColor.GREEN):
        intervals: list[tuple[float, float, int, tuple[abstract.Endpoint, ...]]] = []
        for group, group_color in colors_by_group.items():
            if group_color != color:
                continue
            endpoints = tuple(sorted(endpoints_by_group[group]))
            if len(endpoints) < 2:
                continue
            feeder_xs = [
                virtual_x[endpoint.entity] + _feeder_offset(endpoint.connector)
                for endpoint in endpoints
            ]
            intervals.append((min(feeder_xs), max(feeder_xs), group, endpoints))
        assignments, track_count = _assign_interval_tracks(intervals)
        track_counts[color] = track_count
        for _min_x, _max_x, group, endpoints in intervals:
            route_specs[group] = (color, endpoints, assignments[group])

    red_tracks = track_counts[WireColor.RED]
    green_tracks = track_counts[WireColor.GREEN]
    row_pitch = _row_pitch(red_tracks, green_tracks)
    entities_per_row = _choose_entities_per_row(
        len(ordered_entities),
        row_pitch,
        red_tracks=red_tracks,
        green_tracks=green_tracks,
    )
    entity_rows = max(1, ceil(len(ordered_entities) / entities_per_row))

    positions: dict[int, tuple[float, float]] = {}
    for index, entity in enumerate(ordered_entities):
        row = index // entities_per_row
        logical_col = index % entities_per_row
        physical_col = logical_col if row % 2 == 0 else entities_per_row - 1 - logical_col
        positions[entity.id] = (physical_col * _ENTITY_SPACING, row * row_pitch)

    routes: dict[int, _BaseRoute] = {}
    for group, (color, endpoints, track) in route_specs.items():
        rows = [entity_index[endpoint.entity] // entities_per_row for endpoint in endpoints]
        routes[group] = _BaseRoute(
            group=group,
            color=color,
            endpoints=endpoints,
            track=track,
            start_row=min(rows),
            end_row=max(rows),
        )

    predicted_relays = sum(
        _route_relay_count(
            route,
            positions,
            entity_index,
            entities_per_row,
            row_pitch,
            red_tracks,
        )
        for route in routes.values()
    )
    predicted_width, predicted_height = _predicted_extent(
        entity_rows,
        entities_per_row,
        row_pitch,
        red_tracks=red_tracks,
        green_tracks=green_tracks,
    )
    physical_groups = len(endpoints_by_group)
    routed_groups = len(routes)
    return _FoldedPlan(
        ordered_entities=ordered_entities,
        positions=positions,
        entity_index=entity_index,
        routes=routes,
        red_tracks=red_tracks,
        green_tracks=green_tracks,
        row_pitch=row_pitch,
        entities_per_row=entities_per_row,
        entity_rows=entity_rows,
        preflight=SafeFoldedPreflight(
            physical_groups=physical_groups,
            routed_groups=routed_groups,
            singleton_groups=physical_groups - routed_groups,
            red_tracks=red_tracks,
            green_tracks=green_tracks,
            entity_rows=entity_rows,
            entities_per_row=entities_per_row,
            predicted_relays=predicted_relays,
            predicted_width=predicted_width,
            predicted_height=predicted_height,
        ),
    )


def _assign_interval_tracks(
    intervals: list[tuple[float, float, int, tuple[abstract.Endpoint, ...]]],
) -> tuple[dict[int, int], int]:
    """Minimum deterministic interval partition, then endpoint-weight track identities."""

    active: list[tuple[float, int]] = []
    available_tracks: list[int] = []
    assignments: dict[int, int] = {}
    next_track = 0
    for min_x, max_x, group, _endpoints in sorted(
        intervals,
        key=lambda item: (item[0], item[1], item[2]),
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

    endpoint_weight = {track: 0 for track in range(next_track)}
    for _min_x, _max_x, group, endpoints in intervals:
        endpoint_weight[assignments[group]] += len(endpoints)
    old_tracks = sorted(
        range(next_track),
        key=lambda track: (-endpoint_weight[track], track),
    )
    remap = {old_track: new_track for new_track, old_track in enumerate(old_tracks)}
    return {group: remap[track] for group, track in assignments.items()}, next_track


def _folded_ordered_entities(physical: PhysicalCircuit) -> list[PhysicalEntity]:
    """Place every public marker first so inputs and outputs form one compact front panel."""

    public_ids = [
        *(port.marker_entity for port in physical.inputs),
        *(port.marker_entity for port in physical.outputs),
    ]
    public_set = set(public_ids)
    body_ids = sorted(entity.id for entity in physical.entities if entity.id not in public_set)
    ordered_ids: list[int] = []
    seen: set[int] = set()
    for entity_id in [*public_ids, *body_ids]:
        if entity_id not in seen:
            ordered_ids.append(entity_id)
            seen.add(entity_id)
    return [physical.entity_by_id(entity_id) for entity_id in ordered_ids]


def _row_pitch(red_tracks: int, green_tracks: int) -> float:
    red_extent = _track_extent(red_tracks)
    green_extent = _track_extent(green_tracks)
    required = red_extent + green_extent + _ROW_MARGIN
    return max(_SAFE_PITCH, ceil(required / _SAFE_PITCH) * _SAFE_PITCH)


def _track_extent(track_count: int) -> float:
    if track_count <= 0:
        return 0.0
    return _FIRST_BUS_OFFSET + (track_count - 1) * _TRACK_SPACING


def _portal_outer_offset(red_tracks: int, green_tracks: int) -> float:
    total = red_tracks + green_tracks
    if total <= 0:
        return 0.0
    return _PORTAL_GAP + _PORTAL_FIRST_OFFSET + (total - 1) * _PORTAL_SPACING


def _choose_entities_per_row(
    entity_count: int,
    row_pitch: float,
    *,
    red_tracks: int,
    green_tracks: int,
) -> int:
    if entity_count <= 1:
        return 1
    portal_margin = _portal_outer_offset(red_tracks, green_tracks)
    red_extent = _track_extent(red_tracks)
    green_extent = _track_extent(green_tracks)
    best: tuple[float, float, float, int] | None = None
    for columns in range(1, entity_count + 1):
        rows = ceil(entity_count / columns)
        side_margin = portal_margin if rows > 1 else 0.0
        width = (columns - 1) * _ENTITY_SPACING + 2 * side_margin
        height = (rows - 1) * row_pitch + red_extent + green_extent
        score = (max(width, height), width * height, abs(width - height), columns)
        if best is None or score < best:
            best = score
    assert best is not None
    return best[3]


def _predicted_extent(
    entity_rows: int,
    entities_per_row: int,
    row_pitch: float,
    *,
    red_tracks: int,
    green_tracks: int,
) -> tuple[float, float]:
    portal_margin = (
        _portal_outer_offset(red_tracks, green_tracks) if entity_rows > 1 else 0.0
    )
    width = (entities_per_row - 1) * _ENTITY_SPACING + 2 * portal_margin
    height = (
        (entity_rows - 1) * row_pitch
        + _track_extent(red_tracks)
        + _track_extent(green_tracks)
    )
    return width, height


def _entity_row(plan: _FoldedPlan, entity_id: int) -> int:
    return plan.entity_index[entity_id] // plan.entities_per_row


def _bus_y(row_y: float, color: WireColor, track: int) -> float:
    offset = _FIRST_BUS_OFFSET + track * _TRACK_SPACING
    return row_y - offset if color is WireColor.RED else row_y + offset


def _portal_ordinal(red_tracks: int, color: WireColor, track: int) -> int:
    return track if color is WireColor.RED else red_tracks + track


def _portal_x_values(
    entities_per_row: int,
    red_tracks: int,
    boundary: int,
    color: WireColor,
    track: int,
) -> float:
    ordinal = _portal_ordinal(red_tracks, color, track)
    offset = _PORTAL_GAP + _PORTAL_FIRST_OFFSET + ordinal * _PORTAL_SPACING
    right_edge = (entities_per_row - 1) * _ENTITY_SPACING
    if boundary % 2 == 0:
        return right_edge + offset
    return -offset


def _portal_x(plan: _FoldedPlan, boundary: int, color: WireColor, track: int) -> float:
    """Return the stitch column for the fold after ``boundary`` row."""

    return _portal_x_values(
        plan.entities_per_row,
        plan.red_tracks,
        boundary,
        color,
        track,
    )


def _vertical_feeder_nodes(
    feeder_x: float,
    entity_y: float,
    bus_y: float,
    *,
    group: int,
    add_relay: Any,
    role: str,
) -> list[int]:
    sign = -1.0 if bus_y < entity_y else 1.0
    nodes: list[int] = []
    y = entity_y + sign * _SAFE_PITCH
    while y > bus_y if sign < 0 else y < bus_y:
        nodes.append(add_relay((feeder_x, y), group=group, role=role))
        y += sign * _SAFE_PITCH
    nodes.append(
        add_relay(
            (feeder_x, bus_y),
            group=group,
            role=role.replace("feeder", "endpoint tap"),
        )
    )
    return nodes


def _connect_horizontal_segment(
    attachment_nodes: list[tuple[float, int]],
    bus_y: float,
    *,
    group: int,
    color: WireColor,
    relay_connector: int,
    add_relay: Any,
    add_wire: Any,
) -> None:
    min_x = min(x for x, _node in attachment_nodes)
    max_x = max(x for x, _node in attachment_nodes)
    nodes = list(attachment_nodes)
    x = ceil(min_x / _SAFE_PITCH) * _SAFE_PITCH
    last = floor(max_x / _SAFE_PITCH) * _SAFE_PITCH
    while x <= last + 1e-9:
        nodes.append(
            (
                x,
                add_relay(
                    (x, bus_y),
                    group=group,
                    role=f"{color.value} row bus",
                ),
            )
        )
        x += _SAFE_PITCH
    nodes.sort(key=lambda item: (item[0], item[1]))
    for (_left_x, left_id), (_right_x, right_id) in zip(
        nodes,
        nodes[1:],
        strict=False,
    ):
        add_wire(left_id, relay_connector, right_id, relay_connector, color)


def _route_relay_count(
    route: _BaseRoute,
    positions: dict[int, tuple[float, float]],
    entity_index: dict[int, int],
    entities_per_row: int,
    row_pitch: float,
    red_tracks: int,
) -> int:
    """Return the exact number of relay entities emitted by one folded route."""

    regular_feeders = max(
        0,
        ceil((_FIRST_BUS_OFFSET + route.track * _TRACK_SPACING) / _SAFE_PITCH) - 1,
    )
    count = len(route.endpoints) * (regular_feeders + 1)
    endpoints_by_row: dict[int, list[abstract.Endpoint]] = defaultdict(list)
    for endpoint in route.endpoints:
        endpoints_by_row[entity_index[endpoint.entity] // entities_per_row].append(endpoint)

    for row in range(route.start_row, route.end_row + 1):
        attachment_xs = [
            positions[endpoint.entity][0] + _feeder_offset(endpoint.connector)
            for endpoint in endpoints_by_row.get(row, [])
        ]
        portal_count = 0
        if row > route.start_row:
            attachment_xs.append(
                _portal_x_values(
                    entities_per_row,
                    red_tracks,
                    row - 1,
                    route.color,
                    route.track,
                )
            )
            portal_count += 1
        if row < route.end_row:
            attachment_xs.append(
                _portal_x_values(
                    entities_per_row,
                    red_tracks,
                    row,
                    route.color,
                    route.track,
                )
            )
            portal_count += 1
        if len(attachment_xs) < 2:
            raise AssertionError("folded preflight produced an invalid row segment")
        count += portal_count
        first = ceil(min(attachment_xs) / _SAFE_PITCH)
        last = floor(max(attachment_xs) / _SAFE_PITCH)
        count += max(0, last - first + 1)

    # The two endpoint fold taps were counted as portal attachments above. Count only ordinary
    # six-tile stitch relays between the odd bus rows here.
    bus_offset = _FIRST_BUS_OFFSET + route.track * _TRACK_SPACING
    for boundary in range(route.start_row, route.end_row):
        upper = boundary * row_pitch + (
            -bus_offset if route.color is WireColor.RED else bus_offset
        )
        lower = (boundary + 1) * row_pitch + (
            -bus_offset if route.color is WireColor.RED else bus_offset
        )
        first = ceil(upper / _SAFE_PITCH)
        last = floor(lower / _SAFE_PITCH)
        count += max(0, last - first + 1)
    return count


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
    for wire in wires:
        left = positions[wire.source_entity]
        right = positions[wire.target_entity]
        distance = sqrt((left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2)
        if distance > maximum_span + 1e-9:
            raise AssertionError(
                "safe-folded-crossbar emitted an overlong wire: "
                f"{wire.source_entity}->{wire.target_entity} spans {distance:.3f} > "
                f"{maximum_span:.3f}"
            )
