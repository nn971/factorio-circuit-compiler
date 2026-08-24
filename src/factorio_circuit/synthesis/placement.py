"""Discrete annealed placement policies for physical synthesis.

Placement is expressed on Factorio-compatible grid slots instead of continuous coordinates. The
annealed optimizer treats synthesized electrical networks as hyperedges and rewards layouts in
which every physical net can already be connected through short hops between real entities.
Long-hop relay count and total spanning-tree length are secondary costs.
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
    PhysicalEntity,
)

Position = tuple[float, float]
PlacementStrategy = Literal["annealed", "net-aware", "row"]
_DISCONNECTED_NET_PENALTY = 2000.0
_SUBSTATION_FOOTPRINT = 2.0


@dataclass(frozen=True, slots=True)
class PlacementOptions:
    """Configuration for physical placement.

    ``anchors`` fixes concrete entity ids at exact positions. By default the placer also anchors
    public input/output marker entities in ordered columns on the left/right perimeter; an explicit
    entity anchor overrides the corresponding automatic I/O position.

    Reserved corridors preserve regular walking/power-access gaps between dense computation
    blocks. Layout-only wire relays may use those corridors except for local 2x2 footprints
    reserved at corridor intersections for substations. Horizontal arithmetic/decider combinators
    occupy two tiles, while constant combinators occupy one tile and may share the two one-tile
    subslots of a 2x1 cell.

    ``net-aware`` remains as a compatibility spelling for the previous public strategy name. New
    callers should use ``annealed``.
    """

    strategy: PlacementStrategy = "annealed"
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
        if self.strategy not in {"annealed", "net-aware", "row"}:
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
    # 2x1 cell centres used by arithmetic/decider combinators.
    slots: tuple[Position, ...]
    # Two 1x1 subslots inside each 2x1 cell, used by constant combinators and relay nodes.
    unit_slots: tuple[Position, ...]
    bounds: RelayForbiddenArea
    relay_forbidden_areas: tuple[RelayForbiddenArea, ...]
    x_positions: tuple[float, ...]
    unit_x_positions: tuple[float, ...]
    y_positions: tuple[float, ...]


@dataclass(slots=True)
class _DiscreteOccupancy:
    """Constant-time occupancy for entities constrained to the annealed grid.

    Movable entities are represented by the one-tile subslots they cover. Fixed anchors can be at
    arbitrary coordinates, so they remain a usually-small geometric blocker set.
    """

    entities: dict[int, PhysicalEntity]
    anchors: dict[int, Position]
    units: dict[Position, int] = field(default_factory=dict)

    def add(self, entity_id: int, position: Position) -> None:
        for key in _unit_keys(self.entities[entity_id], position):
            self.units[key] = entity_id

    def remove(self, entity_id: int, position: Position) -> None:
        for key in _unit_keys(self.entities[entity_id], position):
            if self.units.get(key) == entity_id:
                del self.units[key]

    def owners(
        self,
        entity_id: int,
        position: Position,
        *,
        ignore_ids: set[int] | None = None,
    ) -> set[int]:
        ignored = ignore_ids or set()
        return {
            owner
            for key in _unit_keys(self.entities[entity_id], position)
            if (owner := self.units.get(key)) is not None and owner not in ignored
        }

    def clear_of_anchors(
        self,
        entity_id: int,
        position: Position,
        *,
        ignore_ids: set[int] | None = None,
    ) -> bool:
        ignored = ignore_ids or set()
        entity_half = _entity_half_extent(self.entities[entity_id])
        for anchor_id, anchor_position in self.anchors.items():
            if anchor_id in ignored:
                continue
            if _boxes_overlap(
                position,
                entity_half,
                anchor_position,
                _entity_half_extent(self.entities[anchor_id]),
            ):
                return False
        return True

    def is_clear(self, entity_id: int, position: Position) -> bool:
        return not self.owners(entity_id, position) and self.clear_of_anchors(entity_id, position)


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

    return _annealed_plan(
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


def _annealed_plan(
    circuit: PhysicalCircuit,
    abstract_circuit: abstract.AbstractPhysicalCircuit,
    net_groups: dict[int, int],
    *,
    safe_wire_span: float,
    options: PlacementOptions,
) -> PlacementPlan:
    entities = {entity.id: entity for entity in circuit.entities}
    entity_ids = list(entities)
    input_ids = {port.marker_entity for port in circuit.inputs}
    output_ids = {port.marker_entity for port in circuit.outputs}
    io_ids = input_ids | output_ids
    body_ids = (
        entity_ids if not options.anchor_io else [item for item in entity_ids if item not in io_ids]
    )
    minimum_io_rows = max(len(circuit.inputs), len(circuit.outputs), 1) if options.anchor_io else 1

    groups = _physical_net_entities(abstract_circuit, net_groups)
    incident = _incident_groups(entity_ids, groups)
    body_tile_demand = sum(
        1 if isinstance(entities[entity_id], ConstantCombinator) else 2 for entity_id in body_ids
    )
    requested_body_count = max(1, ceil(body_tile_demand / 2))
    initial_body_count = requested_body_count

    while True:
        grid = _candidate_grid(requested_body_count, minimum_io_rows, options)
        auto_io = _automatic_io_anchors(circuit, grid.bounds) if options.anchor_io else {}
        effective_anchors = {**auto_io, **options.anchors}
        _validate_anchors(circuit, effective_anchors)

        movable = [entity_id for entity_id in entity_ids if entity_id not in effective_anchors]
        positions: dict[int, Position] = dict(effective_anchors)
        occupancy = _DiscreteOccupancy(entities, effective_anchors)
        center = _centroid(grid.slots)
        order = sorted(
            movable,
            key=lambda entity_id: (
                0 if not isinstance(entities[entity_id], ConstantCombinator) else 1,
                -sum(max(1, len(groups[group]) - 1) for group in incident[entity_id]),
                entity_id,
            ),
        )

        seeded = True
        for entity_id in order:
            preferred = _preferred_position(entity_id, positions, groups, incident, center)
            candidates = _candidate_positions(entities[entity_id], grid)
            legal = (
                candidate for candidate in candidates if occupancy.is_clear(entity_id, candidate)
            )
            try:
                position = min(legal, key=lambda item: (_distance_sq(item, preferred), item))
            except ValueError:
                seeded = False
                break
            positions[entity_id] = position
            occupancy.add(entity_id, position)

        if seeded:
            break
        requested_body_count += max(4, ceil(max(1, len(movable)) / 8))
        maximum_body_count = initial_body_count + len(effective_anchors) * 16 + 1024
        if requested_body_count > maximum_body_count:
            raise ValueError("placement anchors leave too few legal grid slots")

    if movable:
        iterations = options.iterations
        if iterations is None:
            iterations = 0 if len(movable) < 6 else min(20_000, 30 * len(movable))
        if iterations:
            _anneal(
                entities,
                movable,
                positions,
                grid,
                groups,
                incident,
                safe_wire_span=safe_wire_span,
                center=center,
                iterations=iterations,
                seed=options.random_seed,
            )
            _relax(
                entities,
                movable,
                positions,
                grid,
                groups,
                incident,
                safe_wire_span=safe_wire_span,
                center=center,
                sweeps=3,
            )

    _validate_entity_positions(circuit, positions)
    return PlacementPlan(positions, grid.relay_forbidden_areas)


def _anneal(
    entities: dict[int, PhysicalEntity],
    movable: list[int],
    positions: dict[int, Position],
    grid: _GridGeometry,
    groups: dict[int, tuple[int, ...]],
    incident: dict[int, tuple[int, ...]],
    *,
    safe_wire_span: float,
    center: Position,
    iterations: int,
    seed: int,
) -> None:
    rng = Random(seed)
    movable_set = set(movable)
    anchors = {
        entity_id: position
        for entity_id, position in positions.items()
        if entity_id not in movable_set
    }
    occupancy = _DiscreteOccupancy(entities, anchors)
    for entity_id in movable:
        occupancy.add(entity_id, positions[entity_id])

    if len(grid.unit_slots) > 1:
        span_x = max(grid.unit_x_positions) - min(grid.unit_x_positions)
        span_y = max(grid.y_positions) - min(grid.y_positions)
    else:
        span_x = 1.0
        span_y = 1.0
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
        entity = entities[entity_id]
        current = positions[entity_id]
        candidates = _candidate_positions(entity, grid)

        if rng.random() < 0.12:
            target = candidates[rng.randrange(len(candidates))]
        else:
            preferred = _preferred_position(entity_id, positions, groups, incident, center)
            noise = spatial_scale * (0.30 * normalized_temperature + 0.01)
            target_point = (
                preferred[0] + rng.uniform(-noise, noise),
                preferred[1] + rng.uniform(-noise, noise),
            )
            target = _nearest_candidate(entity, target_point, grid)

        if target == current:
            continue
        owners = occupancy.owners(entity_id, target, ignore_ids={entity_id})
        other: int | None = None
        if owners:
            if len(owners) != 1:
                continue
            candidate_other = next(iter(owners))
            if positions[candidate_other] != target:
                continue
            if _entity_half_extent(entities[candidate_other]) != _entity_half_extent(entity):
                continue
            other = candidate_other
        if not occupancy.clear_of_anchors(entity_id, target):
            continue

        affected = set(incident[entity_id])
        if other is not None:
            affected.update(incident[other])
        before = sum(_group_energy(groups[group], positions, safe_wire_span) for group in affected)
        before += _compactness_energy(entity_id, positions, center)
        if other is not None:
            before += _compactness_energy(other, positions, center)

        occupancy.remove(entity_id, current)
        if other is None:
            positions[entity_id] = target
            occupancy.add(entity_id, target)
        else:
            occupancy.remove(other, target)
            positions[entity_id] = target
            positions[other] = current
            occupancy.add(entity_id, target)
            occupancy.add(other, current)

        after = sum(_group_energy(groups[group], positions, safe_wire_span) for group in affected)
        after += _compactness_energy(entity_id, positions, center)
        if other is not None:
            after += _compactness_energy(other, positions, center)

        delta = after - before
        if delta <= 0 or rng.random() < exp(-delta / energy_temperature):
            current_energy += delta
            if current_energy + 1e-12 < best_energy:
                best_energy = current_energy
                best_positions = {item: positions[item] for item in movable}
            continue

        occupancy.remove(entity_id, target)
        positions[entity_id] = current
        occupancy.add(entity_id, current)
        if other is not None:
            occupancy.remove(other, current)
            positions[other] = target
            occupancy.add(other, target)

    positions.update(best_positions)


def _relax(
    entities: dict[int, PhysicalEntity],
    movable: list[int],
    positions: dict[int, Position],
    grid: _GridGeometry,
    groups: dict[int, tuple[int, ...]],
    incident: dict[int, tuple[int, ...]],
    *,
    safe_wire_span: float,
    center: Position,
    sweeps: int,
) -> None:
    """Finish annealing with deterministic force-directed swap/move sweeps."""

    movable_set = set(movable)
    anchors = {
        entity_id: position
        for entity_id, position in positions.items()
        if entity_id not in movable_set
    }
    occupancy = _DiscreteOccupancy(entities, anchors)
    for entity_id in movable:
        occupancy.add(entity_id, positions[entity_id])

    for _sweep in range(sweeps):
        improved = False
        for entity_id in sorted(movable):
            entity = entities[entity_id]
            current = positions[entity_id]
            preferred = _preferred_position(entity_id, positions, groups, incident, center)
            target = _nearest_candidate(entity, preferred, grid)
            if target == current:
                continue

            owners = occupancy.owners(entity_id, target, ignore_ids={entity_id})
            other: int | None = None
            if owners:
                if len(owners) != 1:
                    continue
                candidate_other = next(iter(owners))
                if positions[candidate_other] != target:
                    continue
                if _entity_half_extent(entities[candidate_other]) != _entity_half_extent(entity):
                    continue
                other = candidate_other
            if not occupancy.clear_of_anchors(entity_id, target):
                continue

            affected = set(incident[entity_id])
            if other is not None:
                affected.update(incident[other])
            before = sum(
                _group_energy(groups[group], positions, safe_wire_span) for group in affected
            )
            before += _compactness_energy(entity_id, positions, center)
            if other is not None:
                before += _compactness_energy(other, positions, center)

            occupancy.remove(entity_id, current)
            if other is None:
                positions[entity_id] = target
                occupancy.add(entity_id, target)
            else:
                occupancy.remove(other, target)
                positions[entity_id] = target
                positions[other] = current
                occupancy.add(entity_id, target)
                occupancy.add(other, current)

            after = sum(
                _group_energy(groups[group], positions, safe_wire_span) for group in affected
            )
            after += _compactness_energy(entity_id, positions, center)
            if other is not None:
                after += _compactness_energy(other, positions, center)

            if after + 1e-12 < before:
                improved = True
                continue

            occupancy.remove(entity_id, target)
            positions[entity_id] = current
            occupancy.add(entity_id, current)
            if other is not None:
                occupancy.remove(other, current)
                positions[other] = target
                occupancy.add(other, target)
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

    x_positions = tuple(x_position(column) for column in range(columns))
    y_positions = tuple(y_position(row) for row in range(rows))
    unit_x_positions = tuple(value for x in x_positions for value in (x - 0.5, x + 0.5))
    slots = tuple((x, y) for y in y_positions for x in x_positions)
    unit_slots = tuple((x, y) for y in y_positions for x in unit_x_positions)
    bounds: RelayForbiddenArea = (
        x_positions[0] - 1.0,
        x_positions[-1] + 1.0,
        y_positions[0] - 0.5,
        y_positions[-1] + 0.5,
    )

    forbidden: list[RelayForbiddenArea] = []
    if options.reserve_corridors and options.corridor_width >= _SUBSTATION_FOOTPRINT:
        vertical_centers = [
            (x_positions[column - 1] + x_positions[column]) / 2.0
            for column in range(columns_per_block, columns, columns_per_block)
        ]
        horizontal_centers = [
            (y_positions[row - 1] + y_positions[row]) / 2.0
            for row in range(rows_per_block, rows, rows_per_block)
        ]
        half = _SUBSTATION_FOOTPRINT / 2.0
        forbidden.extend(
            (x - half, x + half, y - half, y + half)
            for x in vertical_centers
            for y in horizontal_centers
        )

    return _GridGeometry(
        slots,
        unit_slots,
        bounds,
        tuple(forbidden),
        x_positions,
        unit_x_positions,
        y_positions,
    )


def _candidate_positions(entity: object, grid: _GridGeometry) -> tuple[Position, ...]:
    if isinstance(entity, ConstantCombinator):
        return grid.unit_slots
    if isinstance(entity, (ArithmeticCombinator, DeciderCombinator)):
        return grid.slots
    raise TypeError(entity)


def _nearest_candidate(entity: object, point: Position, grid: _GridGeometry) -> Position:
    if isinstance(entity, ConstantCombinator):
        x_choices = grid.unit_x_positions
    elif isinstance(entity, (ArithmeticCombinator, DeciderCombinator)):
        x_choices = grid.x_positions
    else:
        raise TypeError(entity)
    x = min(x_choices, key=lambda value: (abs(value - point[0]), value))
    y = min(grid.y_positions, key=lambda value: (abs(value - point[1]), value))
    return (x, y)


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

    excess_components = _reach_component_count(points, safe_wire_span) - 1
    remaining = set(range(1, len(points)))
    best_edges: dict[int, tuple[tuple[int, float, float, int, int], int, float]] = {}

    def consider(left: int, right: int) -> None:
        distance = _distance(points[left][1], points[right][1])
        relay_count = _relay_estimate(distance, safe_wire_span)
        overreach = max(0.0, distance - safe_wire_span) / safe_wire_span
        key = (relay_count, overreach, distance, points[left][0], points[right][0])
        previous = best_edges.get(right)
        if previous is None or key < previous[0]:
            best_edges[right] = (key, left, distance)

    for right in remaining:
        consider(0, right)

    relays = 0
    total_length = 0.0
    smooth_overreach = 0.0
    while remaining:
        right = min(remaining, key=lambda item: best_edges[item][0])
        key, _left, distance = best_edges.pop(right)
        relay_count, overreach = key[0], key[1]
        remaining.remove(right)
        relays += relay_count
        total_length += distance
        smooth_overreach += overreach**2
        for candidate in remaining:
            consider(right, candidate)

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
    return 0.002 * _distance_sq(positions[entity_id], center)


def _validate_anchors(circuit: PhysicalCircuit, anchors: dict[int, Position]) -> None:
    entities = {entity.id: entity for entity in circuit.entities}
    unknown = sorted(set(anchors) - set(entities))
    if unknown:
        raise ValueError(f"placement anchors reference unknown entity ids: {unknown}")
    anchored = sorted(anchors)
    for index, left_id in enumerate(anchored):
        for right_id in anchored[index + 1 :]:
            if _boxes_overlap(
                anchors[left_id],
                _entity_half_extent(entities[left_id]),
                anchors[right_id],
                _entity_half_extent(entities[right_id]),
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


def _unit_keys(entity: object, position: Position) -> tuple[Position, ...]:
    if isinstance(entity, ConstantCombinator):
        return (position,)
    if isinstance(entity, (ArithmeticCombinator, DeciderCombinator)):
        return ((position[0] - 0.5, position[1]), (position[0] + 0.5, position[1]))
    raise TypeError(entity)


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


def _centroid(points: list[Position] | set[Position] | tuple[Position, ...]) -> Position:
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
