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
        return tuple(left)


@dataclass(frozen=True, slots=True)
class RoutingPlan:
    relays: tuple[BlueprintRelay, ...]
    wires: tuple[RoutedWire, ...]


def route_wires(
    circuit: PhysicalCircuit,
    positions: dict[int, tuple[float, float]],
    *,
    safe_span: float = DEFAULT_SAFE_WIRE_SPAN,
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
        for segment_index, (left_id, right_id) in enumerate(zip(chain, chain[1:])):
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
    validate_entity_clearance(circuit, positions, plan)
    return plan


def _find_relay_positions(
    source: tuple[float, float],
    target: tuple[float, float],
    *,
    safe_span: float,
    occupied: list[tuple[tuple[float, float], tuple[float, float], int]],
    edge_index: int,
) -> list[tuple[float, float]]:
    distance = _distance(source, target)
    dx = target[0] - source[0]
    dy = target[1] - source[1]
    norm = distance or 1.0
    perp = (-dy / norm, dx / norm)

    # Start far enough off the main combinator row to clear a horizontal 2x1 combinator.
    # Alternate sides first, then try more distant lanes. edge_index rotates the ordering so
    # consecutive long connections do not all prefer the same routing lane.
    base_offsets = [2.0, -2.0, 3.25, -3.25, 4.5, -4.5, 5.5, -5.5]
    rotate = (edge_index - 1) % len(base_offsets)
    offsets = base_offsets[rotate:] + base_offsets[:rotate]

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
            if _relay_candidates_are_clear(candidates, occupied):
                return candidates

    raise ValueError(
        "could not route a collision-free circuit wire within the configured reach; "
        "try a larger safe span or improve the layout"
    )


def _chain_is_in_reach(
    source: tuple[float, float],
    relays: list[tuple[float, float]],
    target: tuple[float, float],
    safe_span: float,
) -> bool:
    points = [source, *relays, target]
    return all(_distance(left, right) <= safe_span + 1e-9 for left, right in zip(points, points[1:]))


def _relay_candidates_are_clear(
    candidates: list[tuple[float, float]],
    occupied: list[tuple[tuple[float, float], tuple[float, float], int]],
) -> bool:
    local: list[tuple[tuple[float, float], tuple[float, float], int]] = list(occupied)
    for index, candidate in enumerate(candidates):
        if any(
            _boxes_overlap(candidate, _RELAY_HALF_EXTENT, pos, half)
            for pos, half, _entity_id in local
        ):
            return False
        local.append((candidate, _RELAY_HALF_EXTENT, -(index + 1)))
    return True


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
) -> None:
    """Raise if a generated relay overlaps a compiler entity or another relay."""

    originals = [
        (positions[entity.id], _entity_half_extent(entity), entity.id)
        for entity in circuit.entities
    ]
    relays = [(relay.position, _RELAY_HALF_EXTENT, relay.entity_id) for relay in plan.relays]

    for relay_pos, relay_half, relay_id in relays:
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
        abs(left_pos[0] - right_pos[0])
        < left_half[0] + right_half[0] + _COLLISION_MARGIN
        and abs(left_pos[1] - right_pos[1])
        < left_half[1] + right_half[1] + _COLLISION_MARGIN
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
