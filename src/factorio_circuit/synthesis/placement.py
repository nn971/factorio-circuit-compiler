"""Discrete, net-aware placement policies for physical synthesis.

Placement is deliberately expressed on Factorio-compatible grid slots instead of continuous
coordinates.  The optimizer treats synthesized electrical networks as hyperedges and rewards
layouts in which every physical net can already be connected through short hops between real
entities.  Long-hop relay count and total spanning-tree length are secondary costs.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from math import ceil, exp, hypot, sqrt
from random import Random
from typing import Literal

from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import (
    ArithmeticCombinator,
    ConstantCombinator,
    DeciderCombinator,
    PhysicalCircuit,
)

Position = tuple[float, float]
PlacementStrategy = Literal["net-aware", "row"]
_DISCONNECTED_NET_PENALTY = 2000.0
_SUBSTATION_FOOTPRINT = 2.0


@dataclass(frozen=True, slots=True)
class PlacementOptions:
    """Configuration for physical placement.

    ``anchors`` fixes concrete entity ids at exact positions.  By default the placer also
    anchors public input/output marker entities in ordered columns on the left/right perimeter;
    an explicit entity anchor overrides the corresponding automatic I/O position.

    Reserved corridors are empty of ordinary implementation combinators.  They preserve regular
    walking/power-access gaps between dense computation blocks.  Layout-only wire relays may use
    those corridors except for local 2x2 footprints reserved at corridor intersections for
    substations.  ``block_width_tiles`` and ``block_height_tiles`` describe the dense computation
    block in Factorio tiles.  Horizontal arithmetic/decider combinators occupy two tiles, so the
    default 16x16 block contains eight combinator columns and sixteen rows.  ``corridor_width`` is
    inserted between adjacent blocks.  Routing retries use deterministic placement basins; later
    retries also reduce ``target_fill`` by ``retry_fill_scale`` so a circuit can trade compactness
    for legal relay space.
    """

    strategy: PlacementStrategy = "net-aware"
    anchors: dict[int, Position] = field(default_factory=dict)
    anchor_io: bool = True
    reserve_corridors: bool = True
    block_width_tiles: int = 16
    block_height_tiles: int = 16
    corridor_width: float = 2.0
    target_fill: float = 0.72
    iterations: int | None = None
    random_seed: int = 0
    restarts: int = 3
    retry_fill_scale: float = 0.9

    def validate(self) -> None:
        if self.strategy not in {"net-aware", "row"}:
            raise ValueError(f"unknown placement strategy {self.strategy!r}")
        if self.block_width_tiles <= 0 or self.block_width_tiles % 2 != 0:
            raise ValueError("placement block_width_tiles must be a positive even tile count")
        if self.block_height_tiles <= 0:
            raise ValueError("placement block_height_tiles must be positive")
        if self.corridor_width < 0:
            raise ValueError("corridor width cannot be negative")
        if self.reserve_corridors and 0 < self.corridor_width < _SUBSTATION_FOOTPRINT:
            raise ValueError("reserved corridors must be at least 2 tiles wide for substations")
        if not 0 < self.target_fill <= 1:
            raise ValueError("placement target_fill must be in (0, 1]")
        if self.iterations is not None and self.iterations < 0:
            raise ValueError("placement iterations cannot be negative")
        if self.restarts <= 0:
            raise ValueError("placement restarts must be positive")
        if not 0 < self.retry_fill_scale <= 1:
            raise ValueError("placement retry_fill_scale must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class PlacementMetrics:
    """Cheap geometry metrics used by the placer and layout benchmarks."""

    disconnected_net_components: int
    estimated_relays: int
    mst_wire_length: float
    energy: float


RelayForbiddenArea = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class PlacementPlan:
    """Internal placement result including geometry reserved from relay entities."""

    positions: dict[int, Position]
    relay_forbidden_areas: tuple[RelayForbiddenArea, ...] = ()


@dataclass(frozen=True, slots=True)
class _GridGeometry:
    slots: tuple[Position, ...]
    bounds: RelayForbiddenArea
    relay_forbidden_areas: tuple[RelayForbiddenArea, ...]


def row_positions(circuit: PhysicalCircuit) -> dict[int, Position]:
    """Compatibility baseline: place implementation entities in one horizontal row."""

    input_ids = {port.marker_entity for port in circuit.inputs}
    output_ids = {port.marker_entity for port in circuit.outputs}
    implementation = [
        entity
        for entity in circuit.entities
        if entity.id not in input_ids and entity.id not in output_ids
    ]

    positions: dict[int, Position] = {}
    for index, port in enumerate(circuit.inputs):
        positions[port.marker_entity] = (-4.0, float(index * 2))
    for index, entity in enumerate(implementation):
        positions[entity.id] = (float(index * 2), 0.0)
    right_x = float(max(2, len(implementation) * 2 + 2))
    for index, output_port in enumerate(circuit.outputs):
        positions[output_port.marker_entity] = (right_x, float(index * 2))

    for entity in circuit.entities:
        if entity.id not in positions:
            assert isinstance(entity, ConstantCombinator)
            positions[entity.id] = (0.0, 4.0 + entity.id)
    return positions


def place_physical_circuit(
    circuit: PhysicalCircuit,
    abstract_circuit: abstract.AbstractPhysicalCircuit,
    net_groups: dict[int, int],
    *,
    safe_wire_span: float,
    options: PlacementOptions | None = None,
) -> dict[int, Position]:
    """Place all physical entities using the requested synthesis policy."""

    return plan_physical_circuit(
        circuit,
        abstract_circuit,
        net_groups,
        safe_wire_span=safe_wire_span,
        options=options,
    ).positions


def plan_physical_circuit(
    circuit: PhysicalCircuit,
    abstract_circuit: abstract.AbstractPhysicalCircuit,
    net_groups: dict[int, int],
    *,
    safe_wire_span: float,
    options: PlacementOptions | None = None,
) -> PlacementPlan:
    """Place entities and return any physical areas reserved from relay placement."""

    selected = options or PlacementOptions()
    selected.validate()
    if safe_wire_span <= 0:
        raise ValueError("safe_wire_span must be positive")

    _validate_anchors(circuit, selected.anchors)
    if selected.strategy == "row":
        positions = row_positions(circuit)
        positions.update(selected.anchors)
        _validate_entity_positions(circuit, positions)
        return PlacementPlan(positions)

    return _net_aware_plan(
        circuit,
        abstract_circuit,
        net_groups,
        safe_wire_span=safe_wire_span,
        options=selected,
    )


def placement_metrics(
    abstract_circuit: abstract.AbstractPhysicalCircuit,
    net_groups: dict[int, int],
    positions: dict[int, Position],
    *,
    safe_wire_span: float,
) -> PlacementMetrics:
    """Measure reach-connectivity and approximate relay cost for synthesized physical nets."""

    groups = _physical_net_entities(abstract_circuit, net_groups)
    disconnected = 0
    relays = 0
    length = 0.0
    energy = 0.0
    for entity_ids in groups.values():
        group_metrics = _group_metrics(entity_ids, positions, safe_wire_span)
        disconnected += group_metrics[0]
        relays += group_metrics[1]
        length += group_metrics[2]
        energy += group_metrics[3]
    return PlacementMetrics(disconnected, relays, length, energy)


def _net_aware_plan(
    circuit: PhysicalCircuit,
    abstract_circuit: abstract.AbstractPhysicalCircuit,
    net_groups: dict[int, int],
    *,
    safe_wire_span: float,
    options: PlacementOptions,
) -> PlacementPlan:
    entity_ids = [entity.id for entity in circuit.entities]
    input_ids = {port.marker_entity for port in circuit.inputs}
    output_ids = {port.marker_entity for port in circuit.outputs}
    io_ids = input_ids | output_ids
    body_ids = (
        entity_ids if not options.anchor_io else [item for item in entity_ids if item not in io_ids]
    )
    minimum_io_rows = max(len(circuit.inputs), len(circuit.outputs), 1) if options.anchor_io else 1

    groups = _physical_net_entities(abstract_circuit, net_groups)
    incident = _incident_groups(entity_ids, groups)

    # Keep the computation rectangle stable when explicit anchors are added: size it from the
    # complete body rather than only the currently movable subset.  Grow only if an explicit
    # anchor consumes too many candidate slots.
    requested_body_count = max(1, len(body_ids))
    while True:
        grid = _candidate_grid(requested_body_count, minimum_io_rows, options)
        auto_io = _automatic_io_anchors(circuit, grid.bounds) if options.anchor_io else {}
        effective_anchors = {**auto_io, **options.anchors}
        _validate_anchors(circuit, effective_anchors)

        movable = [entity_id for entity_id in entity_ids if entity_id not in effective_anchors]
        slots = [
            slot for slot in grid.slots if _slot_clear_of_anchors(circuit, slot, effective_anchors)
        ]
        if len(slots) >= len(movable):
            break
        requested_body_count += max(8, len(movable) - len(slots))
        if requested_body_count > max(1, len(body_ids)) + len(effective_anchors) * 16 + 1024:
            raise ValueError("placement anchors leave too few legal grid slots")

    positions: dict[int, Position] = dict(effective_anchors)
    if movable:
        center = _centroid(slots)
        free_slots = set(slots)
        order = sorted(
            movable,
            key=lambda entity_id: (
                -sum(max(1, len(groups[group]) - 1) for group in incident[entity_id]),
                entity_id,
            ),
        )

        # Deterministic greedy seed: highly connected entities go first; later entities are
        # placed near already-placed peers of each incident hyperedge.  Anchored I/O markers
        # therefore pull their directly connected logic toward the correct perimeter.
        for entity_id in order:
            preferred = _preferred_position(entity_id, positions, groups, incident, center)
            slot = min(free_slots, key=lambda item: (_distance_sq(item, preferred), item))
            positions[entity_id] = slot
            free_slots.remove(slot)

        iterations = options.iterations
        if iterations is None:
            iterations = 0 if len(movable) < 6 else min(20_000, 30 * len(movable))
        if iterations:
            _anneal(
                movable,
                positions,
                slots,
                groups,
                incident,
                safe_wire_span=safe_wire_span,
                center=center,
                iterations=iterations,
                seed=options.random_seed,
            )
            _relax(
                movable,
                positions,
                slots,
                groups,
                incident,
                safe_wire_span=safe_wire_span,
                center=center,
                sweeps=3,
            )

    _validate_entity_positions(circuit, positions)
    return PlacementPlan(positions, grid.relay_forbidden_areas)


def _anneal(
    movable: list[int],
    positions: dict[int, Position],
    slots: list[Position],
    groups: dict[int, tuple[int, ...]],
    incident: dict[int, tuple[int, ...]],
    *,
    safe_wire_span: float,
    center: Position,
    iterations: int,
    seed: int,
) -> None:
    rng = Random(seed)
    occupancy = {positions[entity_id]: entity_id for entity_id in movable}
    span_x = max(x for x, _ in slots) - min(x for x, _ in slots) if len(slots) > 1 else 1.0
    span_y = max(y for _, y in slots) - min(y for _, y in slots) if len(slots) > 1 else 1.0
    spatial_scale = max(1.0, span_x, span_y)

    current_energy = sum(
        _group_energy(members, positions, safe_wire_span) for members in groups.values()
    ) + sum(_compactness_energy(entity_id, positions, center) for entity_id in movable)
    best_energy = current_energy
    best_positions = {entity_id: positions[entity_id] for entity_id in movable}

    for step in range(iterations):
        progress = step / max(1, iterations - 1)
        normalized_temperature = 0.03**progress
        energy_temperature = 35.0 * normalized_temperature + 0.05
        entity_id = movable[rng.randrange(len(movable))]
        current = positions[entity_id]

        if rng.random() < 0.12:
            target = slots[rng.randrange(len(slots))]
        else:
            preferred = _preferred_position(entity_id, positions, groups, incident, center)
            noise = spatial_scale * (0.30 * normalized_temperature + 0.01)
            target_point = (
                preferred[0] + rng.uniform(-noise, noise),
                preferred[1] + rng.uniform(-noise, noise),
            )
            target = min(slots, key=lambda item: _distance_sq(item, target_point))

        if target == current:
            continue
        other = occupancy.get(target)
        affected = set(incident[entity_id])
        if other is not None:
            affected.update(incident[other])

        before = sum(_group_energy(groups[group], positions, safe_wire_span) for group in affected)
        before += _compactness_energy(entity_id, positions, center)
        if other is not None:
            before += _compactness_energy(other, positions, center)

        if other is None:
            positions[entity_id] = target
            del occupancy[current]
            occupancy[target] = entity_id
        else:
            positions[entity_id] = target
            positions[other] = current
            occupancy[target] = entity_id
            occupancy[current] = other

        after = sum(_group_energy(groups[group], positions, safe_wire_span) for group in affected)
        after += _compactness_energy(entity_id, positions, center)
        if other is not None:
            after += _compactness_energy(other, positions, center)

        delta = after - before
        accepted = delta <= 0 or rng.random() < exp(-delta / energy_temperature)
        if accepted:
            current_energy += delta
            if current_energy + 1e-12 < best_energy:
                best_energy = current_energy
                best_positions = {item: positions[item] for item in movable}
            continue

        if other is None:
            del occupancy[target]
            occupancy[current] = entity_id
            positions[entity_id] = current
        else:
            occupancy[target] = other
            occupancy[current] = entity_id
            positions[entity_id] = current
            positions[other] = target

    # Annealing deliberately accepts uphill moves, but synthesis should never return a later
    # degraded state merely because the temperature had not quite reached zero.
    positions.update(best_positions)


def _relax(
    movable: list[int],
    positions: dict[int, Position],
    slots: list[Position],
    groups: dict[int, tuple[int, ...]],
    incident: dict[int, tuple[int, ...]],
    *,
    safe_wire_span: float,
    center: Position,
    sweeps: int,
) -> None:
    """Finish annealing with deterministic force-directed swap/move sweeps."""

    occupancy = {positions[entity_id]: entity_id for entity_id in movable}
    for _sweep in range(sweeps):
        improved = False
        for entity_id in sorted(movable):
            current = positions[entity_id]
            preferred = _preferred_position(entity_id, positions, groups, incident, center)
            target = min(slots, key=lambda item: _distance_sq(item, preferred))
            if target == current:
                continue
            other = occupancy.get(target)
            affected = set(incident[entity_id])
            if other is not None:
                affected.update(incident[other])

            before = sum(
                _group_energy(groups[group], positions, safe_wire_span) for group in affected
            )
            before += _compactness_energy(entity_id, positions, center)
            if other is not None:
                before += _compactness_energy(other, positions, center)

            if other is None:
                positions[entity_id] = target
                del occupancy[current]
                occupancy[target] = entity_id
            else:
                positions[entity_id] = target
                positions[other] = current
                occupancy[target] = entity_id
                occupancy[current] = other

            after = sum(
                _group_energy(groups[group], positions, safe_wire_span) for group in affected
            )
            after += _compactness_energy(entity_id, positions, center)
            if other is not None:
                after += _compactness_energy(other, positions, center)

            if after + 1e-12 < before:
                improved = True
                continue

            if other is None:
                del occupancy[target]
                occupancy[current] = entity_id
                positions[entity_id] = current
            else:
                occupancy[target] = other
                occupancy[current] = entity_id
                positions[entity_id] = current
                positions[other] = target
        if not improved:
            break


def _candidate_grid(
    body_count: int,
    minimum_rows: int,
    options: PlacementOptions,
) -> _GridGeometry:
    target_slots = max(1, ceil(body_count / options.target_fill))
    natural_rows = max(1, ceil(sqrt(target_slots * 2.0)))
    rows = max(minimum_rows, natural_rows)
    columns = max(1, ceil(target_slots / rows), ceil(rows / 2.0))

    columns_per_block = options.block_width_tiles // 2
    rows_per_block = options.block_height_tiles

    def x_position(column: int) -> float:
        gap = 0.0
        if options.reserve_corridors:
            gap = (column // columns_per_block) * options.corridor_width
        return float(column * 2) + gap

    def y_position(row: int) -> float:
        gap = 0.0
        if options.reserve_corridors:
            gap = (row // rows_per_block) * options.corridor_width
        return float(row) + gap

    x_positions = [x_position(column) for column in range(columns)]
    y_positions = [y_position(row) for row in range(rows)]
    slots = tuple((x, y) for y in y_positions for x in x_positions)
    bounds: RelayForbiddenArea = (
        x_positions[0] - 1.0,
        x_positions[-1] + 1.0,
        y_positions[0] - 0.5,
        y_positions[-1] + 0.5,
    )

    forbidden: list[RelayForbiddenArea] = []
    if options.reserve_corridors and options.corridor_width >= _SUBSTATION_FOOTPRINT:
        vertical_centers = [
            (x_positions[column - 1] + 1.0 + x_positions[column] - 1.0) / 2.0
            for column in range(columns_per_block, columns, columns_per_block)
        ]
        horizontal_centers = [
            (y_positions[row - 1] + 0.5 + y_positions[row] - 0.5) / 2.0
            for row in range(rows_per_block, rows, rows_per_block)
        ]
        half = _SUBSTATION_FOOTPRINT / 2.0
        forbidden.extend(
            (x - half, x + half, y - half, y + half)
            for x in vertical_centers
            for y in horizontal_centers
        )

    return _GridGeometry(slots, bounds, tuple(forbidden))


def _automatic_io_anchors(
    circuit: PhysicalCircuit,
    bounds: RelayForbiddenArea,
) -> dict[int, Position]:
    """Anchor public interfaces in stable ordered columns on the layout perimeter."""

    left, right, top, bottom = bounds
    center_y = (top + bottom) / 2.0
    anchors: dict[int, Position] = {}

    def column_y_positions(count: int) -> list[float]:
        if count == 0:
            return []
        start = round(center_y - (count - 1) / 2.0)
        return [float(start + index) for index in range(count)]

    input_x = left - 1.0
    for input_port, y in zip(circuit.inputs, column_y_positions(len(circuit.inputs)), strict=True):
        anchors[input_port.marker_entity] = (input_x, y)

    output_x = right + 1.0
    for output_port, y in zip(
        circuit.outputs, column_y_positions(len(circuit.outputs)), strict=True
    ):
        anchors[output_port.marker_entity] = (output_x, y)

    return anchors


def _physical_net_entities(
    circuit: abstract.AbstractPhysicalCircuit,
    net_groups: dict[int, int],
) -> dict[int, tuple[int, ...]]:
    members: dict[int, set[int]] = defaultdict(set)
    for net in circuit.nets:
        group = net_groups[net.id]
        members[group].update(endpoint.entity for endpoint in net.endpoints)
    return {group: tuple(sorted(entity_ids)) for group, entity_ids in members.items()}


def _incident_groups(
    entity_ids: list[int], groups: dict[int, tuple[int, ...]]
) -> dict[int, tuple[int, ...]]:
    result: dict[int, list[int]] = {entity_id: [] for entity_id in entity_ids}
    for group, members in groups.items():
        for entity_id in members:
            result[entity_id].append(group)
    return {entity_id: tuple(sorted(group_ids)) for entity_id, group_ids in result.items()}


def _preferred_position(
    entity_id: int,
    positions: dict[int, Position],
    groups: dict[int, tuple[int, ...]],
    incident: dict[int, tuple[int, ...]],
    fallback: Position,
) -> Position:
    centroids: list[Position] = []
    for group in incident[entity_id]:
        peers = [
            positions[peer] for peer in groups[group] if peer != entity_id and peer in positions
        ]
        if peers:
            centroids.append(_centroid(peers))
    return _centroid(centroids) if centroids else fallback


def _group_metrics(
    entity_ids: tuple[int, ...],
    positions: dict[int, Position],
    safe_wire_span: float,
) -> tuple[int, int, float, float]:
    points = [(entity_id, positions[entity_id]) for entity_id in entity_ids]
    if len(points) <= 1:
        return (0, 0, 0.0, 0.0)

    component_count = _reach_component_count(points, safe_wire_span)
    excess_components = component_count - 1

    connected = {0}
    remaining = set(range(1, len(points)))
    relays = 0
    total_length = 0.0
    smooth_overreach = 0.0
    while remaining:
        best: tuple[tuple[int, float, float, int, int], int, int, float] | None = None
        for left in connected:
            for right in remaining:
                distance = _distance(points[left][1], points[right][1])
                relay_count = _relay_estimate(distance, safe_wire_span)
                overreach = max(0.0, distance - safe_wire_span) / safe_wire_span
                key = (
                    relay_count,
                    overreach,
                    distance,
                    points[left][0],
                    points[right][0],
                )
                if best is None or key < best[0]:
                    best = (key, left, right, distance)
        assert best is not None
        _, _left, right, distance = best
        connected.add(right)
        remaining.remove(right)
        relays += _relay_estimate(distance, safe_wire_span)
        total_length += distance
        smooth_overreach += (max(0.0, distance - safe_wire_span) / safe_wire_span) ** 2

    energy = (
        _DISCONNECTED_NET_PENALTY * excess_components
        + 20.0 * relays
        + 6.0 * smooth_overreach
        + 0.12 * total_length / safe_wire_span
    )
    return (excess_components, relays, total_length, energy)


def _group_energy(
    entity_ids: tuple[int, ...], positions: dict[int, Position], safe_wire_span: float
) -> float:
    return _group_metrics(entity_ids, positions, safe_wire_span)[3]


def _reach_component_count(points: list[tuple[int, Position]], safe_wire_span: float) -> int:
    parent = list(range(len(points)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left in range(len(points)):
        for right in range(left + 1, len(points)):
            if _distance(points[left][1], points[right][1]) <= safe_wire_span + 1e-9:
                union(left, right)
    return len({find(index) for index in range(len(points))})


def _relay_estimate(distance: float, safe_wire_span: float) -> int:
    return max(0, ceil(distance / safe_wire_span - 1e-12) - 1)


def _compactness_energy(entity_id: int, positions: dict[int, Position], center: Position) -> float:
    _ = entity_id
    return 0.002 * _distance_sq(positions[entity_id], center)


def _validate_anchors(circuit: PhysicalCircuit, anchors: dict[int, Position]) -> None:
    entity_ids = {entity.id for entity in circuit.entities}
    unknown = sorted(set(anchors) - entity_ids)
    if unknown:
        raise ValueError(f"placement anchors reference unknown entity ids: {unknown}")
    anchored = sorted(anchors)
    for index, left_id in enumerate(anchored):
        for right_id in anchored[index + 1 :]:
            if _boxes_overlap(
                anchors[left_id],
                _entity_half_extent(circuit.entity_by_id(left_id)),
                anchors[right_id],
                _entity_half_extent(circuit.entity_by_id(right_id)),
            ):
                raise ValueError(f"placement anchors overlap entities {left_id} and {right_id}")


def _validate_entity_positions(circuit: PhysicalCircuit, positions: dict[int, Position]) -> None:
    if set(positions) != {entity.id for entity in circuit.entities}:
        raise ValueError("placement did not assign exactly one position to every physical entity")
    entities = circuit.entities
    for index, left in enumerate(entities):
        for right in entities[index + 1 :]:
            if _boxes_overlap(
                positions[left.id],
                _entity_half_extent(left),
                positions[right.id],
                _entity_half_extent(right),
            ):
                raise ValueError(f"placement overlaps entities {left.id} and {right.id}")


def _slot_clear_of_anchors(
    circuit: PhysicalCircuit, slot: Position, anchors: dict[int, Position]
) -> bool:
    # Candidate slots are dimensioned for a horizontal 2x1 combinator, the largest footprint
    # currently emitted.  Testing with that footprint keeps the same slot valid for any entity.
    slot_half = (1.0, 0.5)
    return all(
        not _boxes_overlap(
            slot,
            slot_half,
            anchor_position,
            _entity_half_extent(circuit.entity_by_id(anchor_id)),
        )
        for anchor_id, anchor_position in anchors.items()
    )


def _entity_half_extent(entity: object) -> tuple[float, float]:
    if isinstance(entity, ConstantCombinator):
        return (0.5, 0.5)
    if isinstance(entity, (ArithmeticCombinator, DeciderCombinator)):
        return (1.0, 0.5)
    raise TypeError(entity)


def _boxes_overlap(
    left_pos: Position,
    left_half: tuple[float, float],
    right_pos: Position,
    right_half: tuple[float, float],
) -> bool:
    return (
        abs(left_pos[0] - right_pos[0]) < left_half[0] + right_half[0] - 1e-9
        and abs(left_pos[1] - right_pos[1]) < left_half[1] + right_half[1] - 1e-9
    )


def _centroid(points: list[Position] | set[Position]) -> Position:
    if not points:
        return (0.0, 0.0)
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _distance(left: Position, right: Position) -> float:
    return hypot(left[0] - right[0], left[1] - right[1])


def _distance_sq(left: Position, right: Position) -> float:
    return (left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2
