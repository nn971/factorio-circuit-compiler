"""Construct a bounded, search-free folded crossbar for physical circuit nets.

This module is intentionally independent from :mod:`safe_crossbar`.  The linear
``safe-crossbar`` remains the canonical correctness reference and rollback path.

The folded strategy keeps the same deterministic entity order, but it does *not*
reuse the linear layout's global bus-track assignment.  Folding extends row
segments to fold portals, so two virtual intervals that are disjoint in the
linear layout can overlap after folding.  To preserve correctness, this module
colors the actual physical horizontal segment intervals independently on every
entity row after all endpoint and portal attachment positions are known.

Cross-row nets use deterministic vertical fold stitches on boundary-local portal
columns.  Bus tracks are packed on adjacent integer rows so every 1x1 relay
constant combinator shares one blueprint-coordinate phase while consuming its
actual footprint rather than a two-tile lane.  Fold portals use adjacent integer
columns while skipping the ordinary six-tile horizontal relay lattice.  Real
implementation entities use a three-tile center pitch, which keeps the +/-2
feeder columns off that same relay lattice even for 2x1 combinators.  There is no
placement search, routing search, retry loop, or backtracking.
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
_ENTITY_SPACING = 3.0
_FEEDER_OFFSET = 2.0
_FIRST_BUS_OFFSET = 3.0
_TRACK_SPACING = 1.0
_RELAY_CENTER_CLEARANCE = 1.1
_ROW_MARGIN = 12.0
_PORTAL_GAP = 6.0
_PORTAL_FIRST_OFFSET = 3.0
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
class _Route:
    group: int
    color: WireColor
    endpoints: tuple[abstract.Endpoint, ...]
    start_row: int
    end_row: int


@dataclass(frozen=True, slots=True)
class _RowSegment:
    group: int
    row: int
    color: WireColor
    endpoint_xs: tuple[float, ...]
    portal_xs: tuple[float, ...]
    min_x: float
    max_x: float


@dataclass(frozen=True, slots=True)
class _FoldedPlan:
    ordered_entities: tuple[PhysicalEntity, ...]
    positions: dict[int, tuple[float, float]]
    entity_index: dict[int, int]
    routes: dict[int, _Route]
    segments: dict[tuple[int, int], _RowSegment]
    segment_tracks: dict[tuple[int, int], int]
    portal_ordinals: dict[tuple[int, int], int]
    boundary_portal_counts: dict[int, int]
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

    ``safe-crossbar`` remains the canonical rollback/reference strategy.  This
    builder checks relay count and footprint before allocating relay objects.
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
            f"singletons={stats.singleton_groups}; row_tracks=red:{stats.red_tracks},"
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
        detail="constructing row-local buses and deterministic fold stitches",
    )

    for completed_routes, group in enumerate(sorted(plan.routes), start=1):
        route = plan.routes[group]
        color = route.color
        relay_connector = _relay_connector_id(color)

        for left, right in zip(route.endpoints, route.endpoints[1:], strict=False):
            physical.connections.append(
                WireConnection(_wire_endpoint(left), _wire_endpoint(right), color)
            )

        endpoints_by_row: dict[int, list[abstract.Endpoint]] = defaultdict(list)
        for endpoint in route.endpoints:
            endpoints_by_row[_entity_row(plan, endpoint.entity)].append(endpoint)

        for row in range(route.start_row, route.end_row + 1):
            segment = plan.segments[(group, row)]
            track = plan.segment_tracks[(group, row)]
            row_y = row * plan.row_pitch
            bus_y = _bus_y(row_y, color, track)
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
                add_wire(
                    endpoint.entity,
                    _real_connector_id(physical, endpoint, color),
                    feeder_nodes[0],
                    relay_connector,
                    color,
                )
                for left_id, right_id in zip(feeder_nodes, feeder_nodes[1:], strict=False):
                    add_wire(left_id, relay_connector, right_id, relay_connector, color)

            if row > route.start_row:
                boundary = row - 1
                portal_x = _portal_x(plan, boundary, group)
                portal_id = add_relay(
                    (portal_x, bus_y),
                    group=group,
                    role=f"{color.value} fold tap",
                )
                bus_nodes.append((portal_x, portal_id))
            if row < route.end_row:
                boundary = row
                portal_x = _portal_x(plan, boundary, group)
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
            if abs(min(x for x, _node in bus_nodes) - segment.min_x) > 1e-9:
                raise AssertionError("folded segment minimum drifted after preflight")
            if abs(max(x for x, _node in bus_nodes) - segment.max_x) > 1e-9:
                raise AssertionError("folded segment maximum drifted after preflight")
            _connect_horizontal_segment(
                bus_nodes,
                bus_y,
                group=group,
                color=color,
                relay_connector=relay_connector,
                add_relay=add_relay,
                add_wire=add_wire,
            )

        for boundary in range(route.start_row, route.end_row):
            portal_x = _portal_x(plan, boundary, group)
            upper_track = plan.segment_tracks[(group, boundary)]
            lower_track = plan.segment_tracks[(group, boundary + 1)]
            upper_bus_y = _bus_y(boundary * plan.row_pitch, color, upper_track)
            lower_bus_y = _bus_y((boundary + 1) * plan.row_pitch, color, lower_track)
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
            y = _first_regular_between(upper_bus_y, lower_bus_y)
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

    _validate_relay_integer_lattice(relays)
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

    route_specs: dict[int, tuple[WireColor, tuple[abstract.Endpoint, ...]]] = {}
    rough_intervals: dict[WireColor, list[tuple[float, float, int, int]]] = {
        WireColor.RED: [],
        WireColor.GREEN: [],
    }
    virtual_x = {
        entity.id: index * _ENTITY_SPACING for index, entity in enumerate(ordered_entities)
    }
    for group, group_color in colors_by_group.items():
        endpoints = tuple(sorted(endpoints_by_group[group]))
        if len(endpoints) < 2:
            continue
        route_specs[group] = (group_color, endpoints)
        feeder_xs = [
            virtual_x[endpoint.entity] + _feeder_offset(endpoint.connector)
            for endpoint in endpoints
        ]
        rough_intervals[group_color].append(
            (min(feeder_xs), max(feeder_xs), group, len(endpoints))
        )

    rough_track_counts = {
        color: _interval_track_count(rough_intervals[color])
        for color in (WireColor.RED, WireColor.GREEN)
    }
    rough_row_pitch = _row_pitch(
        rough_track_counts[WireColor.RED],
        rough_track_counts[WireColor.GREEN],
    )
    cut_crossings = _cut_crossing_counts(
        len(ordered_entities),
        route_specs,
        entity_index,
    )
    entities_per_row = _choose_entities_per_row(
        len(ordered_entities),
        rough_row_pitch,
        cut_crossings=cut_crossings,
    )
    entity_rows = max(1, ceil(len(ordered_entities) / entities_per_row))

    x_positions: dict[int, float] = {}
    for index, entity in enumerate(ordered_entities):
        row = index // entities_per_row
        logical_col = index % entities_per_row
        physical_col = logical_col if row % 2 == 0 else entities_per_row - 1 - logical_col
        x_positions[entity.id] = physical_col * _ENTITY_SPACING

    routes: dict[int, _Route] = {}
    for group, (color, endpoints) in route_specs.items():
        rows = [entity_index[endpoint.entity] // entities_per_row for endpoint in endpoints]
        routes[group] = _Route(
            group=group,
            color=color,
            endpoints=endpoints,
            start_row=min(rows),
            end_row=max(rows),
        )

    crossing_by_boundary: dict[int, list[int]] = defaultdict(list)
    for group, route in routes.items():
        for boundary in range(route.start_row, route.end_row):
            crossing_by_boundary[boundary].append(group)

    portal_ordinals: dict[tuple[int, int], int] = {}
    boundary_portal_counts: dict[int, int] = {}
    for boundary, groups in crossing_by_boundary.items():
        ordered_groups = sorted(groups, key=lambda group: (routes[group].color.value, group))
        boundary_portal_counts[boundary] = len(ordered_groups)
        for ordinal, group in enumerate(ordered_groups):
            portal_ordinals[(group, boundary)] = ordinal

    endpoints_by_group_row: dict[tuple[int, int], list[abstract.Endpoint]] = defaultdict(list)
    for group, route in routes.items():
        for endpoint in route.endpoints:
            row = entity_index[endpoint.entity] // entities_per_row
            endpoints_by_group_row[(group, row)].append(endpoint)

    segments: dict[tuple[int, int], _RowSegment] = {}
    segment_intervals: dict[
        tuple[int, WireColor], list[tuple[float, float, tuple[int, int], int]]
    ] = defaultdict(list)
    for group, route in routes.items():
        for row in range(route.start_row, route.end_row + 1):
            endpoint_xs = tuple(
                x_positions[endpoint.entity] + _feeder_offset(endpoint.connector)
                for endpoint in endpoints_by_group_row.get((group, row), [])
            )
            portal_xs_list: list[float] = []
            if row > route.start_row:
                portal_xs_list.append(
                    _portal_x_values(
                        entities_per_row,
                        boundary=row - 1,
                        ordinal=portal_ordinals[(group, row - 1)],
                    )
                )
            if row < route.end_row:
                portal_xs_list.append(
                    _portal_x_values(
                        entities_per_row,
                        boundary=row,
                        ordinal=portal_ordinals[(group, row)],
                    )
                )
            portal_xs = tuple(portal_xs_list)
            attachment_xs = (*endpoint_xs, *portal_xs)
            if len(attachment_xs) < 2:
                raise AssertionError(
                    f"folded route {group} row {row} has fewer than two preflight attachments"
                )
            segment = _RowSegment(
                group=group,
                row=row,
                color=route.color,
                endpoint_xs=endpoint_xs,
                portal_xs=portal_xs,
                min_x=min(attachment_xs),
                max_x=max(attachment_xs),
            )
            segments[(group, row)] = segment
            weight = len(endpoint_xs) + len(portal_xs)
            segment_intervals[(row, route.color)].append(
                (segment.min_x, segment.max_x, (group, row), weight)
            )

    segment_tracks: dict[tuple[int, int], int] = {}
    row_track_counts: dict[tuple[int, WireColor], int] = {}
    for key, intervals in segment_intervals.items():
        assignments, track_count = _assign_interval_tracks(intervals)
        segment_tracks.update(assignments)
        row_track_counts[key] = track_count

    red_tracks = max(
        (count for (_row, color), count in row_track_counts.items() if color is WireColor.RED),
        default=0,
    )
    green_tracks = max(
        (
            count
            for (_row, color), count in row_track_counts.items()
            if color is WireColor.GREEN
        ),
        default=0,
    )
    row_pitch = _row_pitch(red_tracks, green_tracks)

    positions = {
        entity.id: (
            x_positions[entity.id],
            float(entity_index[entity.id] // entities_per_row) * row_pitch,
        )
        for entity in ordered_entities
    }

    predicted_relays = _predicted_relay_count(
        routes,
        segments,
        segment_tracks,
        positions,
        entity_index,
        entities_per_row,
        row_pitch,
        portal_ordinals,
    )
    max_portals = max(boundary_portal_counts.values(), default=0)
    predicted_width, predicted_height = _predicted_extent(
        entity_rows,
        entities_per_row,
        row_pitch,
        red_tracks=red_tracks,
        green_tracks=green_tracks,
        max_portals=max_portals,
    )
    physical_groups = len(endpoints_by_group)
    routed_groups = len(routes)
    return _FoldedPlan(
        ordered_entities=ordered_entities,
        positions=positions,
        entity_index=entity_index,
        routes=routes,
        segments=segments,
        segment_tracks=segment_tracks,
        portal_ordinals=portal_ordinals,
        boundary_portal_counts=dict(boundary_portal_counts),
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
    intervals: list[tuple[float, float, tuple[int, int], int]],
) -> tuple[dict[tuple[int, int], int], int]:
    """Assign minimum interval tracks, then put attachment-heavy tracks nearest the row."""

    active: list[tuple[float, int]] = []
    available_tracks: list[int] = []
    assignments: dict[tuple[int, int], int] = {}
    next_track = 0
    for min_x, max_x, key, _weight in sorted(
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
        assignments[key] = track
        heappush(active, (max_x + _RELAY_CENTER_CLEARANCE, track))

    track_weight = {track: 0 for track in range(next_track)}
    for _min_x, _max_x, key, weight in intervals:
        track_weight[assignments[key]] += weight
    old_tracks = sorted(
        range(next_track),
        key=lambda track: (-track_weight[track], track),
    )
    remap = {old_track: new_track for new_track, old_track in enumerate(old_tracks)}
    return {key: remap[track] for key, track in assignments.items()}, next_track


def _interval_track_count(intervals: list[tuple[float, float, int, int]]) -> int:
    """Return the minimum interval-track count for rough row-width selection."""

    active: list[float] = []
    maximum = 0
    for min_x, max_x, _group, _weight in sorted(
        intervals,
        key=lambda item: (item[0], item[1], item[2]),
    ):
        while active and active[0] <= min_x + 1e-9:
            heappop(active)
        heappush(active, max_x + _RELAY_CENTER_CLEARANCE)
        maximum = max(maximum, len(active))
    return maximum


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


def _portal_outer_offset(portal_count: int) -> float:
    if portal_count <= 0:
        return 0.0
    return _portal_lane_offset(portal_count - 1)


def _portal_lane_offset(ordinal: int) -> float:
    """Pack 1x1 portal relays one tile apart while skipping x = 0 (mod 6)."""

    if ordinal < 0:
        raise ValueError("portal ordinal must be nonnegative")
    offset = int(_PORTAL_GAP + _PORTAL_FIRST_OFFSET)
    seen = 0
    while seen < ordinal:
        offset += 1
        if offset % int(_SAFE_PITCH) != 0:
            seen += 1
    return float(offset)


def _cut_crossing_counts(
    entity_count: int,
    route_specs: dict[int, tuple[WireColor, tuple[abstract.Endpoint, ...]]],
    entity_index: dict[int, int],
) -> tuple[int, ...]:
    """Count physical routes crossing every virtual cut between ordered entities."""

    delta = [0] * (entity_count + 2)
    for _color, endpoints in route_specs.values():
        indices = [entity_index[endpoint.entity] for endpoint in endpoints]
        first_cut = min(indices) + 1
        stop_cut = max(indices) + 1
        if first_cut < stop_cut:
            delta[first_cut] += 1
            delta[stop_cut] -= 1

    active = 0
    crossings: list[int] = []
    for cut in range(entity_count + 1):
        active += delta[cut]
        crossings.append(active)
    return tuple(crossings)


def _choose_entities_per_row(
    entity_count: int,
    row_pitch: float,
    *,
    cut_crossings: tuple[int, ...],
) -> int:
    if entity_count <= 1:
        return 1
    if len(cut_crossings) != entity_count + 1:
        raise ValueError("cut_crossings must contain one count for every virtual entity cut")

    best: tuple[float, float, float, int] | None = None
    for columns in range(1, entity_count + 1):
        rows = ceil(entity_count / columns)
        # Three-tile entity pitch alternates centers between x = 0 and 3 (mod 6).
        # A multi-row fold needs its outer entity center back on x = 0 (mod 6), so
        # require an odd number of columns. Single-row layouts have no portals.
        if rows > 1 and columns % 2 == 0:
            continue
        max_portals = max(
            (cut_crossings[cut] for cut in range(columns, entity_count, columns)),
            default=0,
        )
        side_margin = _portal_outer_offset(max_portals) if rows > 1 else 0.0
        width = (columns - 1) * _ENTITY_SPACING + 2 * side_margin
        height = rows * row_pitch
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
    max_portals: int,
) -> tuple[float, float]:
    portal_margin = _portal_outer_offset(max_portals) if entity_rows > 1 else 0.0
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


def _portal_x_values(
    entities_per_row: int,
    *,
    boundary: int,
    ordinal: int,
) -> float:
    offset = _portal_lane_offset(ordinal)
    right_edge = (entities_per_row - 1) * _ENTITY_SPACING
    return right_edge + offset if boundary % 2 == 0 else -offset


def _portal_x(plan: _FoldedPlan, boundary: int, group: int) -> float:
    return _portal_x_values(
        plan.entities_per_row,
        boundary=boundary,
        ordinal=plan.portal_ordinals[(group, boundary)],
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


def _predicted_relay_count(
    routes: dict[int, _Route],
    segments: dict[tuple[int, int], _RowSegment],
    segment_tracks: dict[tuple[int, int], int],
    positions: dict[int, tuple[float, float]],
    entity_index: dict[int, int],
    entities_per_row: int,
    row_pitch: float,
    portal_ordinals: dict[tuple[int, int], int],
) -> int:
    """Count unique relay sites exactly, before allocating LayoutRelay objects."""

    endpoints_by_group_row: dict[tuple[int, int], list[abstract.Endpoint]] = defaultdict(list)
    for group, route in routes.items():
        for endpoint in route.endpoints:
            row = entity_index[endpoint.entity] // entities_per_row
            endpoints_by_group_row[(group, row)].append(endpoint)

    count = 0
    for group, route in routes.items():
        sites: set[tuple[float, float]] = set()
        for row in range(route.start_row, route.end_row + 1):
            segment = segments[(group, row)]
            track = segment_tracks[(group, row)]
            bus_y = _bus_y(row * row_pitch, segment.color, track)

            for endpoint in endpoints_by_group_row.get((group, row), []):
                entity_x, entity_y = positions[endpoint.entity]
                feeder_x = entity_x + _feeder_offset(endpoint.connector)
                sign = -1.0 if bus_y < entity_y else 1.0
                y = entity_y + sign * _SAFE_PITCH
                while y > bus_y if sign < 0 else y < bus_y:
                    sites.add((feeder_x, y))
                    y += sign * _SAFE_PITCH
                sites.add((feeder_x, bus_y))

            for portal_x in segment.portal_xs:
                sites.add((portal_x, bus_y))

            x = ceil(segment.min_x / _SAFE_PITCH) * _SAFE_PITCH
            last = floor(segment.max_x / _SAFE_PITCH) * _SAFE_PITCH
            while x <= last + 1e-9:
                sites.add((x, bus_y))
                x += _SAFE_PITCH

        for boundary in range(route.start_row, route.end_row):
            if (group, boundary) not in portal_ordinals:
                raise AssertionError("crossing route is missing a fold portal ordinal")
            portal_x = _portal_x_values(
                entities_per_row,
                boundary=boundary,
                ordinal=portal_ordinals[(group, boundary)],
            )
            upper_track = segment_tracks[(group, boundary)]
            lower_track = segment_tracks[(group, boundary + 1)]
            upper_bus_y = _bus_y(boundary * row_pitch, route.color, upper_track)
            lower_bus_y = _bus_y((boundary + 1) * row_pitch, route.color, lower_track)
            sites.add((portal_x, upper_bus_y))
            sites.add((portal_x, lower_bus_y))
            y = _first_regular_between(upper_bus_y, lower_bus_y)
            while y < lower_bus_y - 1e-9:
                sites.add((portal_x, y))
                y += _SAFE_PITCH

        count += len(sites)
    return count


def _vertical_path_relay_count(entity_y: float, bus_y: float) -> int:
    distance = abs(bus_y - entity_y)
    regular = max(0, ceil(distance / _SAFE_PITCH) - 1)
    return regular + 1


def _horizontal_regular_relay_count(min_x: float, max_x: float) -> int:
    first = ceil(min_x / _SAFE_PITCH)
    last = floor(max_x / _SAFE_PITCH)
    return max(0, last - first + 1)


def _vertical_regular_relay_count(upper_y: float, lower_y: float) -> int:
    y = _first_regular_between(upper_y, lower_y)
    count = 0
    while y < lower_y - 1e-9:
        count += 1
        y += _SAFE_PITCH
    return count


def _first_regular_between(upper_y: float, lower_y: float) -> float:
    if lower_y <= upper_y:
        raise AssertionError("fold stitch must run downward between distinct rows")
    first = ceil(upper_y / _SAFE_PITCH) * _SAFE_PITCH
    if first <= upper_y + 1e-9:
        first += _SAFE_PITCH
    return first


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


def _validate_relay_integer_lattice(relays: list[LayoutRelay]) -> None:
    """Keep every 1x1 routing relay on one Factorio placement-coordinate phase."""

    for relay in relays:
        x, y = relay.position
        if abs(x - round(x)) > 1e-9 or abs(y - round(y)) > 1e-9:
            raise AssertionError(
                "safe-folded-crossbar emitted a relay off the integer blueprint lattice: "
                f"entity {relay.entity_id} at ({x}, {y})"
            )


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
