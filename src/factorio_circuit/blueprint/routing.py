"""Reach-safe routing for Factorio circuit wires in emitted blueprints.

The compiler's physical IR describes logical wire connections without geometry. This module
turns those logical connections into blueprint wires whose individual spans are short enough
for Factorio to preserve when importing the blueprint. Long spans are broken with blank
constant-combinator relays; those relays are a blueprint/layout concern and do not count as
implementation combinators.

Relay placement is also collision-aware. Factorio silently drops/changes invalid blueprint
content in some cases, so the generator must never place a relay on top of a real combinator.
"""

from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import count
from math import ceil, hypot, sqrt

from factorio_circuit.ir.physical import (
    ArithmeticCombinator,
    Connector,
    ConstantCombinator,
    DeciderCombinator,
    PhysicalCircuit,
    WireColor,
    WireEndpoint,
)

# Arithmetic/decider combinators have a nominal circuit-wire reach of 9 tiles in vanilla
# Factorio 2.x. Route more conservatively because the game measures between circuit connector
# anchors rather than merely between entity centres.
VANILLA_COMBINATOR_WIRE_REACH = 9.0
DEFAULT_SAFE_WIRE_SPAN = 7.0

# Conservative collision geometry for the entities currently emitted by the compiler.
# All arithmetic/decider combinators are emitted horizontally (direction=4), hence a 2x1
# footprint. Constant combinators are 1x1. The small margin keeps generated positions away
# from exact collision-box boundaries.
_COLLISION_MARGIN = 0.10
_RELAY_HALF_EXTENT = (0.5, 0.5)


RelayForbiddenArea = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class BlueprintRelay:
    entity_id: int
    position: tuple[float, float]
    description: str


@dataclass(frozen=True, slots=True)
class RoutedWire:
    source_entity: int
    source_connector_id: int
    target_entity: int
    target_connector_id: int
    color: WireColor

    def as_factorio_tuple(self) -> tuple[int, int, int, int]:
        left = [
            self.source_entity,
            self.source_connector_id,
            self.target_entity,
            self.target_connector_id,
        ]
        if left[0] > left[2]:
            left = [left[2], left[3], left[0], left[1]]
        return (left[0], left[1], left[2], left[3])


@dataclass(frozen=True, slots=True)
class RoutingPlan:
    relays: tuple[BlueprintRelay, ...]
    wires: tuple[RoutedWire, ...]


def route_wires(
    circuit: PhysicalCircuit,
    positions: dict[int, tuple[float, float]],
    *,
    safe_span: float = DEFAULT_SAFE_WIRE_SPAN,
    relay_forbidden_areas: tuple[RelayForbiddenArea, ...] = (),
) -> RoutingPlan:
    """Route all logical wires with collision-free blank constant-combinator relays.

    Each logical connection receives dedicated relays, so routing never merges two distinct
    same-colour logical networks. For a long edge, relay candidates are placed on a parallel
    lane offset from the straight source-target segment. Several lane offsets and segment
    counts are tried until every relay is collision-free and every wire segment is in reach.
    """

    if safe_span <= 0:
        raise ValueError("safe_span must be positive")
    if safe_span <= 2.0:
        raise ValueError("safe_span is too small for collision-safe relay routing")

    next_entity_id = max((entity.id for entity in circuit.entities), default=0) + 1
    relays: list[BlueprintRelay] = []
    wires: list[RoutedWire] = []

    occupied: list[tuple[tuple[float, float], tuple[float, float], int]] = []
    for entity in circuit.entities:
        occupied.append((positions[entity.id], _entity_half_extent(entity), entity.id))

    for index, connection in enumerate(circuit.connections, start=1):
        source_pos = positions[connection.source.entity]
        target_pos = positions[connection.target.entity]
        distance = _distance(source_pos, target_pos)

        relay_positions: list[tuple[float, float]] = []
        if distance > safe_span:
            relay_positions = _find_relay_positions(
                source_pos,
                target_pos,
                safe_span=safe_span,
                occupied=occupied,
                edge_index=index,
                forbidden_areas=relay_forbidden_areas,
            )

        relay_ids: list[int] = []
        for position in relay_positions:
            relay_id = next_entity_id
            next_entity_id += 1
            relay_ids.append(relay_id)
            relay = BlueprintRelay(
                entity_id=relay_id,
                position=position,
                description=f"WIRE RELAY — layout-only ({connection.color.value})",
            )
            relays.append(relay)
            occupied.append((position, _RELAY_HALF_EXTENT, relay_id))

        chain = [connection.source.entity, *relay_ids, connection.target.entity]
        for segment_index, (left_id, right_id) in enumerate(zip(chain, chain[1:], strict=False)):
            left_connector = (
                _endpoint_connector_id(circuit, connection.source)
                if segment_index == 0
                else _relay_connector_id(connection.color)
            )
            right_connector = (
                _endpoint_connector_id(circuit, connection.target)
                if segment_index == len(chain) - 2
                else _relay_connector_id(connection.color)
            )
            wires.append(
                RoutedWire(
                    source_entity=left_id,
                    source_connector_id=_colorize_connector(left_connector, connection.color),
                    target_entity=right_id,
                    target_connector_id=_colorize_connector(right_connector, connection.color),
                    color=connection.color,
                )
            )

    plan = RoutingPlan(relays=tuple(relays), wires=tuple(wires))
    all_positions = routed_positions(circuit, positions, plan)
    validate_wire_spans(plan.wires, all_positions, maximum_span=safe_span)
    validate_entity_clearance(
        circuit,
        positions,
        plan,
        relay_forbidden_areas=relay_forbidden_areas,
    )
    return plan


def _find_relay_positions(
    source: tuple[float, float],
    target: tuple[float, float],
    *,
    safe_span: float,
    occupied: list[tuple[tuple[float, float], tuple[float, float], int]],
    edge_index: int,
    forbidden_areas: tuple[RelayForbiddenArea, ...] = (),
) -> list[tuple[float, float]]:
    distance = _distance(source, target)
    dx = target[0] - source[0]
    dy = target[1] - source[1]
    norm = distance or 1.0
    perp = (-dy / norm, dx / norm)

    # Start far enough off the main combinator row to clear a horizontal 2x1 combinator.
    # Alternate sides first, then try more distant lanes. edge_index rotates the ordering so
    # consecutive long connections do not all prefer the same routing lane.
    max_offset_step = int((safe_span - 0.25) * 4)
    base_offsets = [
        direction * step / 4 for step in range(5, max_offset_step + 1) for direction in (1, -1)
    ]
    rotate = (edge_index - 1) % len(base_offsets)
    offsets = [0.0, *base_offsets[rotate:], *base_offsets[:rotate]]

    for offset in offsets:
        abs_offset = abs(offset)
        if abs_offset >= safe_span - 0.25:
            continue

        # The first and last segments travel both along the edge and out to the parallel lane.
        # Pick enough segments that those diagonal segments still fit comfortably inside reach.
        longitudinal_budget = sqrt(max(0.25, safe_span * safe_span - abs_offset * abs_offset))
        minimum_segments = max(2, ceil(distance / longitudinal_budget))

        # Trying a few denser subdivisions also shifts relay positions along the lane and often
        # resolves an otherwise accidental collision with another relay.
        for segments in range(minimum_segments, minimum_segments + 5):
            candidates = []
            for relay_index in range(1, segments):
                fraction = relay_index / segments
                candidates.append(
                    (
                        source[0] + dx * fraction + perp[0] * offset,
                        source[1] + dy * fraction + perp[1] * offset,
                    )
                )

            if not _chain_is_in_reach(source, candidates, target, safe_span):
                continue
            if _relay_candidates_are_clear(candidates, occupied, forbidden_areas=forbidden_areas):
                return candidates

    fallback = _find_grid_relay_positions(
        source,
        target,
        safe_span=safe_span,
        occupied=occupied,
        forbidden_areas=forbidden_areas,
    )
    if fallback is not None:
        return fallback

    raise ValueError(
        "could not route a collision-free circuit wire within the configured reach; "
        "parallel lanes and grid search were both exhausted"
    )


def _find_grid_relay_positions(
    source: tuple[float, float],
    target: tuple[float, float],
    *,
    safe_span: float,
    occupied: list[tuple[tuple[float, float], tuple[float, float], int]],
    forbidden_areas: tuple[RelayForbiddenArea, ...] = (),
) -> list[tuple[float, float]] | None:
    """Find a relay chain on a half-tile lattice when straight parallel lanes fail.

    Circuit wires may cross entities and other wires; only relay entities themselves need free
    collision boxes.  This makes the residual routing problem a graph search over legal relay
    centres rather than a conventional obstacle-avoiding trace.  Placement may additionally
    reserve physical areas from relay entities; wires are still free to cross those areas.
    """

    offsets = _grid_route_offsets(safe_span)
    if not offsets:
        return None

    occupied_positions = [position for position, _half, _entity_id in occupied]
    min_x = min(source[0], target[0], *(position[0] for position in occupied_positions))
    max_x = max(source[0], target[0], *(position[0] for position in occupied_positions))
    min_y = min(source[1], target[1], *(position[1] for position in occupied_positions))
    max_y = max(source[1], target[1], *(position[1] for position in occupied_positions))

    clear_cache: dict[tuple[float, float], bool] = {}

    def relay_is_clear(position: tuple[float, float]) -> bool:
        cached = clear_cache.get(position)
        if cached is not None:
            return cached
        clear = not any(
            _boxes_overlap(position, _RELAY_HALF_EXTENT, other_position, half_extent)
            for other_position, half_extent, _entity_id in occupied
        ) and not _relay_overlaps_forbidden(position, forbidden_areas)
        clear_cache[position] = clear
        return clear

    serial = count()
    expansion_limit = max(6_000, min(40_000, 120 * max(1, len(occupied))))
    for margin_scale in (1.0, 2.0, 4.0):
        margin = safe_span * margin_scale + 1.0
        bounds = (min_x - margin, max_x + margin, min_y - margin, max_y + margin)
        frontier: list[tuple[float, int, int, tuple[float, float]]] = []
        start_hops = _relay_lower_bound(_distance(source, target), safe_span)
        heappush(frontier, (float(start_hops), 0, next(serial), source))
        best_hops: dict[tuple[float, float], int] = {source: 0}
        parent: dict[tuple[float, float], tuple[float, float]] = {}
        expansions = 0

        while frontier and expansions < expansion_limit:
            _priority, hops, _serial, current = heappop(frontier)
            if best_hops.get(current) != hops:
                continue
            expansions += 1

            if _distance(current, target) <= safe_span + 1e-9:
                path: list[tuple[float, float]] = []
                cursor = current
                while cursor != source:
                    path.append(cursor)
                    cursor = parent[cursor]
                path.reverse()
                if _chain_is_in_reach(
                    source, path, target, safe_span
                ) and _relay_candidates_are_clear(path, occupied, forbidden_areas=forbidden_areas):
                    return path

            for dx, dy in offsets:
                candidate = _snap_half_tile((current[0] + dx, current[1] + dy))
                if candidate == current:
                    continue
                if not (
                    bounds[0] <= candidate[0] <= bounds[1]
                    and bounds[2] <= candidate[1] <= bounds[3]
                ):
                    continue
                if _distance(current, candidate) > safe_span + 1e-9:
                    continue
                if not relay_is_clear(candidate):
                    continue

                next_hops = hops + 1
                if next_hops >= best_hops.get(candidate, 1 << 30):
                    continue
                best_hops[candidate] = next_hops
                parent[candidate] = current
                remaining = _distance(candidate, target)
                heuristic = _relay_lower_bound(remaining, safe_span)
                tie_break = remaining / max(safe_span, 1e-9) * 1e-4
                heappush(
                    frontier,
                    (next_hops + heuristic + tie_break, next_hops, next(serial), candidate),
                )

    return None


def _grid_route_offsets(safe_span: float) -> tuple[tuple[float, float], ...]:
    """Return every half-tile relay hop that fits inside the configured wire reach.

    The previous fallback sampled only a few angular directions.  In a dense placement that can
    disconnect the *search graph* even when the real half-tile relay graph is connected: the only
    collision-free first hop may have an unsampled slope.  Enumerating the complete local lattice
    makes failure mean no route was found within the searched area, rather than no sampled route.
    """

    maximum_half_steps = int((safe_span + 1e-9) * 2)
    offsets: list[tuple[float, float]] = []
    for dx_steps in range(-maximum_half_steps, maximum_half_steps + 1):
        for dy_steps in range(-maximum_half_steps, maximum_half_steps + 1):
            if dx_steps == 0 and dy_steps == 0:
                continue
            candidate = (dx_steps / 2.0, dy_steps / 2.0)
            distance = _distance((0.0, 0.0), candidate)
            if 1.25 <= distance <= safe_span + 1e-9:
                offsets.append(candidate)

    return tuple(
        sorted(
            offsets,
            key=lambda item: (-_distance((0.0, 0.0), item), item[0], item[1]),
        )
    )


def _relay_lower_bound(distance: float, safe_span: float) -> int:
    return max(0, ceil(distance / safe_span - 1e-12) - 1)


def _snap_half_tile(position: tuple[float, float]) -> tuple[float, float]:
    return (round(position[0] * 2) / 2, round(position[1] * 2) / 2)


def _chain_is_in_reach(
    source: tuple[float, float],
    relays: list[tuple[float, float]],
    target: tuple[float, float],
    safe_span: float,
) -> bool:
    points = [source, *relays, target]
    return all(
        _distance(left, right) <= safe_span + 1e-9
        for left, right in zip(points, points[1:], strict=False)
    )


def _relay_candidates_are_clear(
    candidates: list[tuple[float, float]],
    occupied: list[tuple[tuple[float, float], tuple[float, float], int]],
    *,
    forbidden_areas: tuple[RelayForbiddenArea, ...] = (),
) -> bool:
    local: list[tuple[tuple[float, float], tuple[float, float], int]] = list(occupied)
    for index, candidate in enumerate(candidates):
        if _relay_overlaps_forbidden(candidate, forbidden_areas):
            return False
        if any(
            _boxes_overlap(candidate, _RELAY_HALF_EXTENT, pos, half)
            for pos, half, _entity_id in local
        ):
            return False
        local.append((candidate, _RELAY_HALF_EXTENT, -(index + 1)))
    return True


def _relay_overlaps_forbidden(
    position: tuple[float, float],
    forbidden_areas: tuple[RelayForbiddenArea, ...],
) -> bool:
    """Return whether a 1x1 relay would intrude into any reserved physical area."""

    x, y = position
    half_x, half_y = _RELAY_HALF_EXTENT
    return any(
        x + half_x > left + 1e-9
        and x - half_x < right - 1e-9
        and y + half_y > top + 1e-9
        and y - half_y < bottom - 1e-9
        for left, right, top, bottom in forbidden_areas
    )


def routed_positions(
    circuit: PhysicalCircuit,
    positions: dict[int, tuple[float, float]],
    plan: RoutingPlan,
) -> dict[int, tuple[float, float]]:
    result = dict(positions)
    result.update({relay.entity_id: relay.position for relay in plan.relays})
    return result


def validate_wire_spans(
    wires: tuple[RoutedWire, ...],
    positions: dict[int, tuple[float, float]],
    *,
    maximum_span: float = DEFAULT_SAFE_WIRE_SPAN,
    tolerance: float = 1e-9,
) -> None:
    """Raise if any emitted wire is longer than the configured conservative centre span."""

    for wire in wires:
        distance = _distance(positions[wire.source_entity], positions[wire.target_entity])
        if distance > maximum_span + tolerance:
            raise ValueError(
                f"wire {wire.source_entity}->{wire.target_entity} spans {distance:.3f} tiles; "
                f"maximum configured span is {maximum_span:.3f}"
            )


def validate_entity_clearance(
    circuit: PhysicalCircuit,
    positions: dict[int, tuple[float, float]],
    plan: RoutingPlan,
    *,
    relay_forbidden_areas: tuple[RelayForbiddenArea, ...] = (),
) -> None:
    """Raise if a generated relay overlaps a compiler entity or another relay."""

    originals = [
        (positions[entity.id], _entity_half_extent(entity), entity.id)
        for entity in circuit.entities
    ]
    relays = [(relay.position, _RELAY_HALF_EXTENT, relay.entity_id) for relay in plan.relays]

    for relay_pos, relay_half, relay_id in relays:
        if _relay_overlaps_forbidden(relay_pos, relay_forbidden_areas):
            raise ValueError(f"wire relay {relay_id} overlaps a reserved placement corridor")
        for pos, half, entity_id in originals:
            if _boxes_overlap(relay_pos, relay_half, pos, half):
                raise ValueError(f"wire relay {relay_id} overlaps entity {entity_id}")
        for other_pos, other_half, other_id in relays:
            if other_id <= relay_id:
                continue
            if _boxes_overlap(relay_pos, relay_half, other_pos, other_half):
                raise ValueError(f"wire relay {relay_id} overlaps relay {other_id}")


def _entity_half_extent(entity: object) -> tuple[float, float]:
    if isinstance(entity, ConstantCombinator):
        return (0.5, 0.5)
    if isinstance(entity, (ArithmeticCombinator, DeciderCombinator)):
        # All emitted combinators use direction=4 (horizontal 2x1 footprint).
        return (1.0, 0.5)
    raise TypeError(entity)


def _boxes_overlap(
    left_pos: tuple[float, float],
    left_half: tuple[float, float],
    right_pos: tuple[float, float],
    right_half: tuple[float, float],
) -> bool:
    return (
        abs(left_pos[0] - right_pos[0]) < left_half[0] + right_half[0] + _COLLISION_MARGIN
        and abs(left_pos[1] - right_pos[1]) < left_half[1] + right_half[1] + _COLLISION_MARGIN
    )


def _endpoint_connector_id(circuit: PhysicalCircuit, endpoint: WireEndpoint) -> int:
    entity = circuit.entity_by_id(endpoint.entity)

    if isinstance(entity, ConstantCombinator):
        if endpoint.connector is not Connector.SINGLE:
            raise ValueError("constant combinator only has a single circuit connector")
        return 1
    if isinstance(entity, (ArithmeticCombinator, DeciderCombinator)):
        if endpoint.connector is Connector.INPUT:
            return 1
        if endpoint.connector is Connector.OUTPUT:
            return 3
        raise ValueError("arithmetic/decider endpoint must be INPUT or OUTPUT")
    raise TypeError(entity)


def _relay_connector_id(color: WireColor) -> int:
    # A constant combinator has one circuit connector. The colour offset is applied separately.
    _ = color
    return 1


def _colorize_connector(red_connector_id: int, color: WireColor) -> int:
    # Factorio 2.x connector IDs are input red/green = 1/2, output red/green = 3/4.
    return red_connector_id if color is WireColor.RED else red_connector_id + 1


def _distance(left: tuple[float, float], right: tuple[float, float]) -> float:
    return hypot(left[0] - right[0], left[1] - right[1])
