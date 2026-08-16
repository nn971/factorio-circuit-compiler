"""Constructive, search-free physical layout for supported Factorio circuits.

The safe crossbar deliberately spends area and relay entities to make materialization predictable.
All implementation entities are placed on one sparse horizontal row. Every synthesized RED physical
network receives one horizontal bus above that row; every GREEN network receives one horizontal bus
below it. Each concrete combinator connector reaches its owning bus through a unique vertical feeder
column.

The geometry uses a six-tile lattice under the compiler's default seven-tile conservative wire span.
Implementation entities sit at ``x = 0 (mod 6)``. INPUT/SINGLE feeders use ``x = -2 (mod 6)`` and
OUTPUT feeders use ``x = +2 (mod 6)``. Ordinary feeder relays use ``y = 0 (mod 6)`` away from the
entity row, while bus rows use ``y = 3 (mod 6)``. Ordinary bus relays remain at ``x = 0 (mod 6)``;
only the owning endpoint inserts a tap at its feeder column. Unrelated feeder/bus crossings therefore
contain no relay, and every generated relay site is separated from other relay sites and real entities
by construction.

This policy does not call the normal placement optimizer or collision-avoiding wire router. It is a
correctness fallback, not a compactness strategy.
"""

from __future__ import annotations

from collections import defaultdict
from math import ceil, floor, sqrt
from typing import Any, cast

from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import (
    ArithmeticCombinator,
    Connector,
    ConstantCombinator,
    DeciderCombinator,
    PhysicalCircuit,
    SignalId,
    WireColor,
    WireConnection,
    WireEndpoint,
)
from factorio_circuit.progress import ProgressCallback, report_progress
from factorio_circuit.synthesis.layout import Layout, LayoutRelay, LayoutWire

_SAFE_PITCH = 6.0
_ENTITY_SPACING = 6.0
_FEEDER_OFFSET = 2.0
_FIRST_BUS_OFFSET = 3.0
# Endpoint -> first regular feeder relay is sqrt(2^2 + 6^2).
_MINIMUM_SAFE_SPAN = sqrt(_FEEDER_OFFSET**2 + _SAFE_PITCH**2)


def safe_crossbar_options() -> Any:
    """Return a PlacementOptions instance selecting the joint safe-crossbar synthesis policy.

    ``PlacementStrategy`` still names only the older optimizer-specific strategies. Keeping the cast
    here localizes that temporary typing gap until the next physical-synthesis API cleanup.
    """

    from factorio_circuit.synthesis.placement import PlacementOptions

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
) -> Layout:
    """Materialize ``physical`` using deterministic bus/feeder geometry.

    The supported subset currently assumes the compiler's ordinary horizontal combinators and blank
    constant-combinator relays, no user-specified fixed entity coordinates, and a conservative wire
    span large enough for the six-tile construction. Within that subset there is no geometric search
    or retry path: every relay coordinate follows directly from entity, endpoint, and physical-net
    order.
    """

    if safe_wire_span + 1e-9 < _MINIMUM_SAFE_SPAN:
        raise ValueError(
            "safe-crossbar requires blueprint_safe_wire_span >= "
            f"{_MINIMUM_SAFE_SPAN:.3f}; got {safe_wire_span:.3f}"
        )

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

    # High-fanout groups are deliberately kept closest to the entity row. This does not affect the
    # correctness proof, but minimizes the weighted vertical feeder length for this fixed bus scheme.
    bus_y_by_group: dict[int, float] = {}
    for color, sign in ((WireColor.RED, -1.0), (WireColor.GREEN, 1.0)):
        groups = [group for group, group_color in colors_by_group.items() if group_color is color]
        groups.sort(key=lambda group: (-len(endpoints_by_group[group]), group))
        for index, group in enumerate(groups):
            bus_y_by_group[group] = sign * (_FIRST_BUS_OFFSET + index * _SAFE_PITCH)

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
    total_groups = len(endpoints_by_group)
    completed_groups = 0
    report_progress(
        progress,
        "safe-layout",
        completed=0,
        total=total_groups,
        detail="constructing fixed physical-net buses",
    )

    for group in sorted(endpoints_by_group):
        endpoints = tuple(sorted(endpoints_by_group[group]))
        color = colors_by_group[group]
        if len(endpoints) >= 2:
            for left, right in zip(endpoints, endpoints[1:], strict=False):
                physical.connections.append(
                    WireConnection(_wire_endpoint(left), _wire_endpoint(right), color)
                )

            bus_y = bus_y_by_group[group]
            tap_nodes: list[tuple[float, int]] = []

            for endpoint in endpoints:
                entity_x, _entity_y = positions[endpoint.entity]
                feeder_x = entity_x + _feeder_offset(endpoint.connector)
                tap_position = (feeder_x, bus_y)
                tap_id = add_relay(tap_position, group=group, role=f"{color.value} tap")
                tap_nodes.append((feeder_x, tap_id))

                feeder_nodes: list[int] = []
                sign = -1 if bus_y < 0 else 1
                y = sign * _SAFE_PITCH
                while (y > bus_y if sign < 0 else y < bus_y):
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
                add_wire(
                    endpoint.entity,
                    real_connector,
                    first,
                    relay_connector,
                    color,
                )
                for left_id, right_id in zip(feeder_nodes, feeder_nodes[1:], strict=False):
                    add_wire(
                        left_id,
                        relay_connector,
                        right_id,
                        relay_connector,
                        color,
                    )

            min_x = min(x for x, _relay_id in tap_nodes)
            max_x = max(x for x, _relay_id in tap_nodes)
            trunk_nodes = list(tap_nodes)
            first_regular = ceil(min_x / _SAFE_PITCH) * _SAFE_PITCH
            last_regular = floor(max_x / _SAFE_PITCH) * _SAFE_PITCH
            x = first_regular
            while x <= last_regular + 1e-9:
                trunk_nodes.append(
                    (
                        x,
                        add_relay(
                            (x, bus_y),
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

        completed_groups += 1
        report_progress(
            progress,
            "safe-layout",
            completed=completed_groups,
            total=total_groups,
            detail=f"relays={len(relays)}; wires={len(wires)}",
        )

    all_positions = dict(positions)
    all_positions.update({relay.entity_id: relay.position for relay in relays})
    _validate_wire_spans(wires, all_positions, maximum_span=safe_wire_span)

    report_progress(
        progress,
        "safe-layout",
        completed=total_groups,
        total=total_groups,
        detail=f"complete; entities={len(physical.entities)}; relays={len(relays)}",
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


def _ordered_entities(physical: PhysicalCircuit) -> list[object]:
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
