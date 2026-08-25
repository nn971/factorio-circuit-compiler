"""Fail-safe optimization of an already-valid physical :class:`Layout`.

This module is the input/output boundary for physical-layout optimization.  Unlike constructive
placement, it does not need an Abstract Physical circuit and does not infer a seed topology.  The
complete routed layout supplied by the caller is validated, converted to the annealer's incremental
state, and retained as the initial best-known candidate.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from math import hypot, sqrt

from factorio_circuit.blueprint import routing as wire_routing
from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import (
    ArithmeticCombinator,
    ConstantCombinator,
    DeciderCombinator,
    PhysicalCircuit,
    WireColor,
    WireEndpoint,
)
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis import joint_layout as exact
from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.layout import Layout, LayoutRelay, LayoutWire
from factorio_circuit.synthesis.placement import PlacementOptions, Position, RelayForbiddenArea

_EPSILON = 1e-9
_MIN_TERMINAL_RELAY_CHOICES = 16


@dataclass(frozen=True, slots=True)
class LegalPlacementLattice:
    """Explicit legal sites and reserved areas for one physical optimization run."""

    unit_sites: tuple[Position, ...]
    wide_sites: tuple[Position, ...]
    forbidden_areas: tuple[RelayForbiddenArea, ...] = ()


@dataclass(frozen=True, slots=True)
class LayoutOptimizationProblem:
    """A complete valid embedding plus the physical constraints the optimizer must preserve."""

    layout: Layout
    lattice: LegalPlacementLattice
    safe_wire_span: float = wire_routing.DEFAULT_SAFE_WIRE_SPAN
    fixed_positions: Mapping[int, Position] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PhysicalLayoutMetrics:
    implementation_entities: int
    relay_count: int
    occupied_area: float
    wire_length: float

    @property
    def objective(self) -> tuple[int, float, float]:
        return (self.relay_count, self.occupied_area, self.wire_length)


@dataclass(frozen=True, slots=True)
class LayoutOptimizationResult:
    layout: Layout
    before: PhysicalLayoutMetrics
    after: PhysicalLayoutMetrics
    proposal_budget: int
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _ValidatedEmbedding:
    state: exact._JointState
    topology: incremental._FeasibleTopology


class _DisjointSet:
    def __init__(self) -> None:
        self._parent: dict[tuple[int, int], tuple[int, int]] = {}

    def add(self, item: tuple[int, int]) -> None:
        self._parent.setdefault(item, item)

    def find(self, item: tuple[int, int]) -> tuple[int, int]:
        self.add(item)
        parent = self._parent[item]
        if parent != item:
            self._parent[item] = self.find(parent)
        return self._parent[item]

    def union(self, left: tuple[int, int], right: tuple[int, int]) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self._parent[max(left_root, right_root)] = min(left_root, right_root)


def validate_physical_layout(problem: LayoutOptimizationProblem) -> None:
    """Validate the exact artifact that optimization would consume and later serialize."""

    _validated_embedding(problem)


def physical_layout_metrics(layout: Layout) -> PhysicalLayoutMetrics:
    """Measure the optimizer's public lexicographic objective on serialized coordinates."""

    relay_ids = {relay.entity_id for relay in layout.relays}
    entities = {entity.id: entity for entity in layout.circuit.entities}
    left = float("inf")
    right = float("-inf")
    top = float("inf")
    bottom = float("-inf")
    for entity_id, (x, y) in layout.positions.items():
        half_x, half_y = (
            (0.5, 0.5)
            if entity_id in relay_ids
            else base_placement._entity_half_extent(entities[entity_id])
        )
        left = min(left, x - half_x)
        right = max(right, x + half_x)
        top = min(top, y - half_y)
        bottom = max(bottom, y + half_y)
    area = 0.0 if not layout.positions else (right - left) * (bottom - top)
    wire_length = sum(
        _distance(layout.positions[wire.source_entity], layout.positions[wire.target_entity])
        for wire in layout.wires
    )
    return PhysicalLayoutMetrics(
        implementation_entities=layout.circuit.combinator_count,
        relay_count=len(layout.relays),
        occupied_area=area,
        wire_length=wire_length,
    )


def optimize_physical_layout(
    problem: LayoutOptimizationProblem,
    *,
    options: PlacementOptions,
) -> LayoutOptimizationResult:
    """Try to improve a valid layout and always return a validated best-known artifact.

    A zero proposal budget is an exact pass-through after validation.  Positive-budget optimizer
    failures are transactional: the validated input remains the result rather than becoming a
    compilation failure.
    """

    options.validate()
    validated = _validated_embedding(problem)
    before = physical_layout_metrics(problem.layout)
    movable_count = sum(
        entity.id not in problem.fixed_positions for entity in problem.layout.circuit.entities
    ) + sum(relay.entity_id not in problem.fixed_positions for relay in problem.layout.relays)
    proposal_budget = options.iterations
    if proposal_budget is None:
        proposal_budget = 0 if movable_count < 6 else min(20_000, 30 * movable_count)
    if options.iterations == 0:
        return LayoutOptimizationResult(problem.layout, before, before, proposal_budget)

    state = validated.state
    topology = validated.topology
    original_state = state
    original_topology = topology
    best_layout = problem.layout
    best_metrics = before
    grid = _lattice_grid(problem.lattice)
    anneal_options = replace(
        options,
        anchors={},
        anchor_io=False,
        iterations=proposal_budget,
        restarts=1,
    )
    diagnostics: list[str] = []
    if proposal_budget >= incremental._EPOCH_PROPOSALS:
        state, topology, coarse_diagnostic = _try_coarse_compaction(state, topology, grid)
        if coarse_diagnostic is not None:
            diagnostics.append(coarse_diagnostic)
        elif state is not original_state:
            try:
                coarse_layout = _materialize_layout(problem.layout, state, topology.routing)
                _validated_embedding(replace(problem, layout=coarse_layout))
            except ValueError as exc:
                diagnostics.append(f"coarse compaction artifact rejected: {exc}")
                state = original_state
                topology = original_topology
            else:
                coarse_metrics = physical_layout_metrics(coarse_layout)
                if coarse_metrics.objective < best_metrics.objective:
                    best_layout = coarse_layout
                    best_metrics = coarse_metrics

    try:
        optimized_topology = incremental._anneal_feasible(
            state,
            topology,
            anneal_options,
            grid,
            diagnostics,
        )
        candidate = _materialize_layout(problem.layout, state, optimized_topology.routing)
        candidate_problem = replace(problem, layout=candidate)
        _validated_embedding(candidate_problem)
    except ValueError as exc:
        diagnostics.append(f"annealing candidate rejected: {exc}")
        return LayoutOptimizationResult(
            best_layout,
            before,
            best_metrics,
            proposal_budget,
            tuple(diagnostics),
        )

    after = physical_layout_metrics(candidate)
    if after.objective > best_metrics.objective:
        diagnostics.append("final candidate was valid but did not improve the physical objective")
        return LayoutOptimizationResult(
            best_layout,
            before,
            best_metrics,
            proposal_budget,
            tuple(diagnostics),
        )
    return LayoutOptimizationResult(candidate, before, after, proposal_budget, tuple(diagnostics))


def _validated_embedding(problem: LayoutOptimizationProblem) -> _ValidatedEmbedding:
    layout = problem.layout
    circuit = layout.circuit
    if problem.safe_wire_span <= 0:
        raise ValueError("safe wire span must be positive")
    if not problem.lattice.unit_sites:
        raise ValueError("legal placement lattice must contain at least one unit site")
    if len(set(problem.lattice.unit_sites)) != len(problem.lattice.unit_sites):
        raise ValueError("legal placement lattice contains duplicate unit sites")
    if len(set(problem.lattice.wide_sites)) != len(problem.lattice.wide_sites):
        raise ValueError("legal placement lattice contains duplicate wide sites")

    entity_ids = [entity.id for entity in circuit.entities]
    if len(set(entity_ids)) != len(entity_ids):
        raise ValueError("physical circuit contains duplicate entity ids")
    relay_ids = [relay.entity_id for relay in layout.relays]
    if len(set(relay_ids)) != len(relay_ids):
        raise ValueError("physical layout contains duplicate relay ids")
    if set(entity_ids) & set(relay_ids):
        raise ValueError("implementation entity ids and layout relay ids overlap")
    all_ids = set(entity_ids) | set(relay_ids)
    if set(layout.positions) != all_ids:
        raise ValueError("layout positions must exactly cover implementation entities and relays")
    relay_positions = {relay.entity_id: relay.position for relay in layout.relays}
    if any(layout.positions[item] != relay_positions[item] for item in relay_positions):
        raise ValueError("layout relay coordinates disagree with serialized positions")

    fixed = dict(problem.fixed_positions)
    if not set(fixed) <= all_ids:
        raise ValueError("fixed placement refers to an unknown physical object")
    for object_id, position in fixed.items():
        if layout.positions[object_id] != position:
            raise ValueError(f"fixed object {object_id} is not at its required exact position")

    unit_sites = set(problem.lattice.unit_sites)
    wide_sites = set(problem.lattice.wide_sites)
    entities = {entity.id: entity for entity in circuit.entities}
    for entity_id, entity in entities.items():
        if entity_id in fixed:
            continue
        legal = unit_sites if isinstance(entity, ConstantCombinator) else wide_sites
        if layout.positions[entity_id] not in legal:
            raise ValueError(f"implementation entity {entity_id} is not on a legal lattice site")
    for relay_id, position in relay_positions.items():
        if relay_id not in fixed and position not in unit_sites:
            raise ValueError(f"layout relay {relay_id} is not on a legal unit site")

    half_extents = {
        **{
            entity_id: base_placement._entity_half_extent(entity)
            for entity_id, entity in entities.items()
        },
        **{relay_id: (0.5, 0.5) for relay_id in relay_ids},
    }
    _validate_object_clearance(layout.positions, half_extents, problem.lattice.forbidden_areas)

    routing = wire_routing.RoutingPlan(
        relays=tuple(
            wire_routing.BlueprintRelay(relay.entity_id, relay.position, relay.description)
            for relay in layout.relays
        ),
        wires=tuple(
            wire_routing.RoutedWire(
                wire.source_entity,
                wire.source_connector_id,
                wire.target_entity,
                wire.target_connector_id,
                wire.color,
            )
            for wire in layout.wires
        ),
    )
    wire_routing.validate_wire_spans(
        routing.wires,
        layout.positions,
        maximum_span=problem.safe_wire_span,
    )
    endpoints_by_group, colors_by_group, relay_groups = _validate_electrical_topology(
        circuit,
        routing,
        set(relay_ids),
    )
    state = exact._JointState(
        circuit=circuit,
        endpoints_by_group=endpoints_by_group,
        colors_by_group=colors_by_group,
        positions={entity_id: layout.positions[entity_id] for entity_id in entity_ids},
        relay_positions=relay_positions,
        relay_groups=relay_groups,
        safe_span=problem.safe_wire_span,
        forbidden_areas=problem.lattice.forbidden_areas,
        fixed_objects=frozenset(fixed),
    )
    topology = incremental._FeasibleTopology.build(state, routing)
    return _ValidatedEmbedding(state, topology)


def _try_coarse_compaction(
    state: exact._JointState,
    topology: incremental._FeasibleTopology,
    grid: base_placement._GridGeometry,
) -> tuple[exact._JointState, incremental._FeasibleTopology, str | None]:
    """Transactionally reseed movable implementation entities into a compact legal region."""

    if state.fixed_objects & state.relay_positions.keys():
        return state, topology, "coarse compaction skipped because a relay is fixed"
    candidate_positions = _coarse_implementation_positions(state, grid)
    if candidate_positions is None or candidate_positions == state.positions:
        return state, topology, "coarse compaction found no alternate legal implementation seed"

    candidate = exact._JointState(
        circuit=state.circuit,
        endpoints_by_group=state.endpoints_by_group,
        colors_by_group=state.colors_by_group,
        positions=candidate_positions,
        relay_positions={},
        relay_groups={},
        safe_span=state.safe_span,
        forbidden_areas=state.forbidden_areas,
        fixed_objects=state.fixed_objects,
    )
    repair_grid = _bounded_routing_grid(
        grid,
        candidate_positions,
        state.safe_span,
        footprint_margin_scale=2.0,
    )
    exposed, exposure_failure = _expose_unroutable_components(candidate, repair_grid)
    if exposure_failure is not None:
        return (
            state,
            topology,
            f"coarse compaction terminal exposure rejected after {exposed} moves: "
            f"{exposure_failure}",
        )
    candidate_positions = dict(candidate.positions)
    routing_grid = _bounded_routing_grid(grid, candidate_positions, state.safe_span)
    try:
        candidate_topology = incremental._construct_feasible_bootstrap(candidate, routing_grid)
        candidate_topology = incremental._simplify_feasible_topology(
            candidate,
            candidate_topology,
        )
    except ValueError as exc:
        return state, topology, f"coarse compaction routing rejected: {exc}"

    center = incremental._centroid([*state.positions.values(), *state.relay_positions.values()])
    candidate_center = incremental._centroid(
        [*candidate.positions.values(), *candidate.relay_positions.values()]
    )
    if incremental._exact_score(candidate, candidate_topology, candidate_center) < (
        incremental._exact_score(state, topology, center)
    ):
        return candidate, candidate_topology, None
    return state, topology, "coarse compaction candidate did not improve the physical objective"


def _expose_unroutable_components(
    state: exact._JointState,
    grid: base_placement._GridGeometry,
) -> tuple[int, str | None]:
    """Evacuate only buried terminals whose net component actually needs a relay path."""

    peer_groups = _entity_net_peer_groups(state)
    entities = {entity.id: entity for entity in state.circuit.entities}
    moves = 0
    for _attempt in range(len(state.positions)):
        occupancy = incremental._SpatialOccupancy.build(state)
        free_sites = {
            site
            for site in grid.unit_slots
            if not incremental._box_overlaps_occupancy(
                occupancy,
                site,
                (0.5, 0.5),
                ignored=set(),
            )
        }
        workspace = incremental._RelayWorkspace.build(free_sites, state.safe_span)
        buried = _first_buried_net_component(state, workspace)
        if buried is None:
            return moves, None

        movable = sorted(
            (entity_id for entity_id in buried if entity_id not in state.fixed_objects),
            key=lambda entity_id: (len(peer_groups.get(entity_id, ())), entity_id),
        )
        if not movable:
            return moves, "the buried component contains only fixed terminals"
        relocated = False
        for entity_id in movable:
            current = state.positions[entity_id]
            occupancy.remove(entity_id, current)
            free_after_removal = {
                site
                for site in grid.unit_slots
                if not incremental._box_overlaps_occupancy(
                    occupancy,
                    site,
                    (0.5, 0.5),
                    ignored=set(),
                )
            }
            workspace_after_removal = incremental._RelayWorkspace.build(
                free_after_removal,
                state.safe_span,
            )
            half = state.object_half_extent(entity_id)
            left, right, top, bottom = incremental._occupied_envelope(
                state,
                excluded={entity_id},
                include_relays=False,
            )
            candidates = (
                grid.unit_slots
                if isinstance(entities[entity_id], ConstantCombinator)
                else grid.slots
            )
            front_panel = _fixed_public_front_panel(state)

            def is_exposed_perimeter(
                candidate: Position,
                *,
                half: tuple[float, float] = half,
                left: float = left,
                right: float = right,
                top: float = top,
                bottom: float = bottom,
                occupancy: incremental._SpatialOccupancy = occupancy,
                entity_id: int = entity_id,
                front_panel: tuple[Position, Position] | None = front_panel,
                workspace: incremental._RelayWorkspace = workspace_after_removal,
            ) -> bool:
                perimeter_gaps: list[float] = []
                if candidate[0] + half[0] <= left + _EPSILON:
                    perimeter_gaps.append(left - (candidate[0] + half[0]))
                if candidate[0] - half[0] >= right - _EPSILON:
                    perimeter_gaps.append(candidate[0] - half[0] - right)
                if candidate[1] + half[1] <= top + _EPSILON:
                    perimeter_gaps.append(top - (candidate[1] + half[1]))
                if candidate[1] - half[1] >= bottom - _EPSILON:
                    perimeter_gaps.append(candidate[1] - half[1] - bottom)
                if (
                    not perimeter_gaps
                    or min(perimeter_gaps) > 2.0 + _EPSILON
                    or occupancy.overlaps(entity_id, candidate, ignored=set())
                ):
                    return False
                if front_panel is not None:
                    panel_center, forward = front_panel
                    projection = (candidate[0] - panel_center[0]) * forward[0] + (
                        candidate[1] - panel_center[1]
                    ) * forward[1]
                    if projection < 2.0 - _EPSILON:
                        return False
                relay_choices = 0
                for site in workspace.nearby_sites(candidate):
                    if (
                        base_placement._boxes_overlap(
                            candidate,
                            half,
                            site,
                            (0.5, 0.5),
                        )
                        or _distance(candidate, site) > state.safe_span + _EPSILON
                    ):
                        continue
                    relay_choices += 1
                    if relay_choices >= _MIN_TERMINAL_RELAY_CHOICES:
                        return True
                return False

            legal = [candidate for candidate in candidates if is_exposed_perimeter(candidate)]
            if not legal:
                occupancy.add(entity_id, current)
                continue

            groups = peer_groups.get(entity_id, ())

            def relocation_key(
                candidate: Position,
                *,
                groups: tuple[frozenset[int], ...] = groups,
                half: tuple[float, float] = half,
                left: float = left,
                right: float = right,
                top: float = top,
                bottom: float = bottom,
            ) -> tuple[float, int, float, Position]:
                distances = [
                    min(_distance(candidate, state.positions[peer]) for peer in group)
                    for group in groups
                ]
                violations = sum(distance > state.safe_span + _EPSILON for distance in distances)
                excess = sum(max(0.0, distance - state.safe_span) for distance in distances)
                expanded = (
                    min(left, candidate[0] - half[0]),
                    max(right, candidate[0] + half[0]),
                    min(top, candidate[1] - half[1]),
                    max(bottom, candidate[1] + half[1]),
                )
                area = (expanded[1] - expanded[0]) * (expanded[3] - expanded[2])
                return (area, violations, excess, candidate)

            state.positions[entity_id] = min(legal, key=relocation_key)
            moves += 1
            relocated = True
            break
        if not relocated:
            return moves, f"no exposed legal site exists for terminals {movable}"
    return moves, "repair did not converge"


def _first_buried_net_component(
    state: exact._JointState,
    workspace: incremental._RelayWorkspace,
) -> frozenset[int] | None:
    """Find a disconnected direct-reach component with no reachable free relay site."""

    for group in sorted(state.endpoints_by_group):
        remaining = {endpoint.entity for endpoint in state.endpoints_by_group[group]}
        components: list[frozenset[int]] = []
        while remaining:
            frontier = [min(remaining)]
            component: set[int] = set()
            while frontier:
                entity_id = frontier.pop()
                if entity_id not in remaining:
                    continue
                remaining.remove(entity_id)
                component.add(entity_id)
                position = state.positions[entity_id]
                frontier.extend(
                    other
                    for other in remaining
                    if _distance(position, state.positions[other]) <= state.safe_span + _EPSILON
                )
            components.append(frozenset(component))
        if len(components) <= 1:
            continue
        for component in components:
            relay_choices = {
                site
                for entity_id in component
                for site in workspace.nearby_sites(state.positions[entity_id])
                if _distance(state.positions[entity_id], site) <= state.safe_span + _EPSILON
            }
            if len(relay_choices) < _MIN_TERMINAL_RELAY_CHOICES:
                return component
    return None


def _coarse_implementation_positions(
    state: exact._JointState,
    grid: base_placement._GridGeometry,
) -> dict[int, Position] | None:
    """Pack entities by physical-net traversal while retaining generic routing channels."""

    fixed = {
        entity_id: position
        for entity_id, position in state.positions.items()
        if entity_id in state.fixed_objects
    }
    placement_state = exact._JointState(
        circuit=state.circuit,
        endpoints_by_group=state.endpoints_by_group,
        colors_by_group=state.colors_by_group,
        positions=dict(fixed),
        relay_positions={},
        relay_groups={},
        safe_span=state.safe_span,
        forbidden_areas=state.forbidden_areas,
        fixed_objects=state.fixed_objects,
    )
    occupancy = incremental._SpatialOccupancy.build(placement_state)
    center = _coarse_target_center(state)
    wide_candidates = list(grid.slots)
    unit_candidates = list(grid.unit_slots)
    front_panel = _fixed_public_front_panel(state)
    if front_panel is not None:
        panel_center, forward = front_panel

        def circuit_side(position: Position) -> bool:
            return (position[0] - panel_center[0]) * forward[0] + (
                position[1] - panel_center[1]
            ) * forward[1] >= 2.0 - _EPSILON

        wide_candidates = [position for position in wide_candidates if circuit_side(position)]
        unit_candidates = [position for position in unit_candidates if circuit_side(position)]

    movable: list[int] = []
    for entity in state.circuit.entities:
        if entity.id not in state.fixed_objects:
            movable.append(entity.id)
    pool_limit = max(64, len(movable) * 8)

    def compact_pool(candidates: list[Position]) -> list[Position]:
        nearest = sorted(
            candidates,
            key=lambda position: (
                (position[0] - center[0]) ** 2 + (position[1] - center[1]) ** 2,
                position,
            ),
        )[:pool_limit]
        return sorted(nearest, key=lambda position: (position[1], position[0]))

    candidates_by_width = {
        0.5: compact_pool(unit_candidates),
        1.0: compact_pool(wide_candidates),
    }
    result = dict(fixed)
    entities = {entity.id: entity for entity in state.circuit.entities}
    order = _net_aware_entity_order(state, movable)
    peer_groups = _entity_net_peer_groups(state)
    source_center = incremental._centroid([state.positions[entity_id] for entity_id in movable])
    candidate_points = [
        *candidates_by_width[0.5],
        *candidates_by_width[1.0],
    ]
    target_scale = _coarse_geometry_scale(state, movable, candidate_points)
    for entity_id in order:
        half_width = base_placement._entity_half_extent(entities[entity_id])[0]
        original = state.positions[entity_id]
        geometry_target = (
            center[0] + (original[0] - source_center[0]) * target_scale,
            center[1] + (original[1] - source_center[1]) * target_scale,
        )
        placed_groups = [
            [result[peer] for peer in group if peer in result]
            for group in peer_groups.get(entity_id, ())
        ]
        placed_groups = [group for group in placed_groups if group]
        placed_peers = [position for group in placed_groups for position in group]
        preferred = incremental._centroid(placed_peers) if placed_peers else geometry_target

        def candidate_key(
            candidate: Position,
            *,
            placed_groups: list[list[Position]] = placed_groups,
            preferred: Position = preferred,
        ) -> tuple[int, float, float, float, Position]:
            group_distances = [
                min(_distance(candidate, peer) for peer in group) for group in placed_groups
            ]
            violations = sum(distance > state.safe_span + _EPSILON for distance in group_distances)
            excess = sum(max(0.0, distance - state.safe_span) for distance in group_distances)
            return (
                violations,
                excess,
                sum(group_distances),
                (candidate[0] - preferred[0]) ** 2 + (candidate[1] - preferred[1]) ** 2,
                candidate,
            )

        legal = (
            position
            for position in candidates_by_width[half_width]
            if not occupancy.overlaps(entity_id, position, ignored=set())
        )
        try:
            position = min(legal, key=candidate_key)
        except ValueError:
            return None
        result[entity_id] = position
        placement_state.positions[entity_id] = position
        occupancy.add(entity_id, position)
    return result


def _coarse_geometry_scale(
    state: exact._JointState,
    movable: list[int],
    candidates: list[Position],
) -> float:
    if not movable or not candidates:
        return 1.0
    source_x = [state.positions[entity_id][0] for entity_id in movable]
    source_y = [state.positions[entity_id][1] for entity_id in movable]
    target_x = [position[0] for position in candidates]
    target_y = [position[1] for position in candidates]
    source_width = max(source_x) - min(source_x)
    source_height = max(source_y) - min(source_y)
    target_width = max(target_x) - min(target_x)
    target_height = max(target_y) - min(target_y)
    scales = [1.0]
    if source_width > _EPSILON:
        scales.append(0.65 * target_width / source_width)
    if source_height > _EPSILON:
        scales.append(0.65 * target_height / source_height)
    return min(scales)


def _entity_net_peer_groups(
    state: exact._JointState,
) -> dict[int, tuple[frozenset[int], ...]]:
    groups: dict[int, list[frozenset[int]]] = defaultdict(list)
    for endpoints in state.endpoints_by_group.values():
        members = {endpoint.entity for endpoint in endpoints}
        for entity_id in members:
            peers = frozenset(members - {entity_id})
            if peers:
                groups[entity_id].append(peers)
    return {entity_id: tuple(items) for entity_id, items in groups.items()}


def _coarse_target_center(state: exact._JointState) -> Position:
    front_panel = _fixed_public_front_panel(state)
    fixed_positions = (
        [front_panel[0]]
        if front_panel is not None
        else [
            state.positions[object_id]
            for object_id in state.fixed_objects
            if object_id in state.positions
        ]
    )
    if not fixed_positions:
        return incremental._centroid(list(state.positions.values()))

    fixed_center = incremental._centroid(fixed_positions)
    movable_positions = [
        position
        for entity_id, position in state.positions.items()
        if entity_id not in state.fixed_objects
    ]
    if not movable_positions:
        return fixed_center
    movable_center = incremental._centroid(movable_positions)
    dx = movable_center[0] - fixed_center[0]
    dy = movable_center[1] - fixed_center[1]
    length = hypot(dx, dy)
    if length <= _EPSILON:
        dx, dy, length = 0.0, 1.0, 1.0
    entities = {entity.id: entity for entity in state.circuit.entities}
    footprint_units = sum(
        1 if isinstance(entities[entity_id], ConstantCombinator) else 2
        for entity_id in state.positions
        if entity_id not in state.fixed_objects
    )
    offset = 2.0 + 1.25 * sqrt(max(1, footprint_units))
    return (
        fixed_center[0] + dx / length * offset,
        fixed_center[1] + dy / length * offset,
    )


def _fixed_public_front_panel(
    state: exact._JointState,
) -> tuple[Position, Position] | None:
    """Infer the circuit-facing half-plane from fixed public markers and seed geometry."""

    public_ids = {
        *(port.marker_entity for port in state.circuit.inputs),
        *(port.marker_entity for port in state.circuit.outputs),
    }
    panel_ids = sorted(public_ids & state.fixed_objects & state.positions.keys())
    if not panel_ids:
        return None
    panel_positions = [state.positions[entity_id] for entity_id in panel_ids]
    panel_center = incremental._centroid(panel_positions)
    body_positions = [
        position
        for entity_id, position in state.positions.items()
        if entity_id not in state.fixed_objects
    ]
    if not body_positions:
        return None
    body_center = incremental._centroid(body_positions)
    dx = body_center[0] - panel_center[0]
    dy = body_center[1] - panel_center[1]
    length = hypot(dx, dy)
    if length <= _EPSILON:
        return panel_center, (0.0, 1.0)
    if len(panel_positions) >= 2:
        left, right = max(
            (
                (left, right)
                for index, left in enumerate(panel_positions)
                for right in panel_positions[index + 1 :]
            ),
            key=lambda pair: _distance(*pair),
        )
        tangent_x = right[0] - left[0]
        tangent_y = right[1] - left[1]
        tangent_length = hypot(tangent_x, tangent_y)
        if tangent_length > _EPSILON:
            normal = (-tangent_y / tangent_length, tangent_x / tangent_length)
            if normal[0] * dx + normal[1] * dy < 0.0:
                normal = (-normal[0], -normal[1])
            return panel_center, normal
    return panel_center, (dx / length, dy / length)


def _net_aware_entity_order(state: exact._JointState, movable: list[int]) -> list[int]:
    """Traverse the physical-net hypergraph so related entities enter nearby compact sites."""

    movable_set = set(movable)
    groups_by_entity: dict[int, list[int]] = defaultdict(list)
    members_by_group: dict[int, tuple[int, ...]] = {}
    for group, endpoints in state.endpoints_by_group.items():
        members = tuple(sorted({endpoint.entity for endpoint in endpoints}))
        members_by_group[group] = members
        for entity_id in members:
            groups_by_entity[entity_id].append(group)

    def geometry_key(entity_id: int) -> tuple[float, float, int]:
        x, y = state.positions[entity_id]
        return (y, x, entity_id)

    result: list[int] = []
    remaining = set(movable)
    visited_groups: set[int] = set()
    fixed_groups = {
        group for entity_id in state.fixed_objects for group in groups_by_entity.get(entity_id, ())
    }
    anchored_frontier = sorted(
        {
            entity_id
            for group in fixed_groups
            for entity_id in members_by_group[group]
            if entity_id in movable_set
        },
        key=geometry_key,
    )
    queue: deque[int] = deque(anchored_frontier)

    while remaining:
        if not queue:
            queue.append(min(remaining, key=geometry_key))
        entity_id = queue.popleft()
        if entity_id not in remaining:
            continue
        remaining.remove(entity_id)
        result.append(entity_id)
        origin = state.positions[entity_id]
        for group in sorted(groups_by_entity.get(entity_id, ())):
            if group in visited_groups:
                continue
            visited_groups.add(group)
            neighbors = [
                candidate for candidate in members_by_group[group] if candidate in remaining
            ]
            neighbors.sort(
                key=lambda candidate: (
                    (state.positions[candidate][0] - origin[0]) ** 2
                    + (state.positions[candidate][1] - origin[1]) ** 2,
                    geometry_key(candidate),
                )
            )
            queue.extend(neighbors)
    return result


def _bounded_routing_grid(
    grid: base_placement._GridGeometry,
    positions: Mapping[int, Position],
    safe_span: float,
    *,
    footprint_margin_scale: float = 1.0,
) -> base_placement._GridGeometry:
    """Restrict a coarse routing transaction to a bounded region around its candidate."""

    margin: float = max(
        2.0 * safe_span,
        footprint_margin_scale * len(positions) ** 0.5,
    )
    left: float = min(x for x, _y in positions.values()) - margin
    right: float = max(x for x, _y in positions.values()) + margin
    top: float = min(y for _x, y in positions.values()) - margin
    bottom: float = max(y for _x, y in positions.values()) + margin

    def inside(position: Position) -> bool:
        return left <= position[0] <= right and top <= position[1] <= bottom

    slots = tuple(position for position in grid.slots if inside(position))
    unit_slots = tuple(position for position in grid.unit_slots if inside(position))
    x_positions = tuple(sorted({x for x, _y in slots}))
    unit_x_positions = tuple(sorted({x for x, _y in unit_slots}))
    y_positions = tuple(sorted({y for _x, y in (*slots, *unit_slots)}))
    return base_placement._GridGeometry(
        slots=slots,
        unit_slots=unit_slots,
        bounds=(left, right, top, bottom),
        relay_forbidden_areas=tuple(
            area
            for area in grid.relay_forbidden_areas
            if area[1] >= left and area[0] <= right and area[3] >= top and area[2] <= bottom
        ),
        x_positions=x_positions,
        unit_x_positions=unit_x_positions,
        y_positions=y_positions,
    )


def _validate_object_clearance(
    positions: Mapping[int, Position],
    half_extents: Mapping[int, tuple[float, float]],
    forbidden_areas: tuple[RelayForbiddenArea, ...],
) -> None:
    ordered = sorted(
        positions,
        key=lambda object_id: positions[object_id][0] - half_extents[object_id][0],
    )
    active: list[int] = []
    for object_id in ordered:
        position = positions[object_id]
        half = half_extents[object_id]
        left = position[0] - half[0]
        active = [
            other_id
            for other_id in active
            if positions[other_id][0] + half_extents[other_id][0] > left + _EPSILON
        ]
        for other_id in active:
            if base_placement._boxes_overlap(
                position,
                half,
                positions[other_id],
                half_extents[other_id],
            ):
                raise ValueError(f"physical objects {other_id} and {object_id} overlap")
        active.append(object_id)

        x, y = position
        half_x, half_y = half
        if any(
            x + half_x > forbidden_left + _EPSILON
            and x - half_x < forbidden_right - _EPSILON
            and y + half_y > forbidden_top + _EPSILON
            and y - half_y < forbidden_bottom - _EPSILON
            for forbidden_left, forbidden_right, forbidden_top, forbidden_bottom in forbidden_areas
        ):
            raise ValueError(f"physical object {object_id} overlaps a reserved area")


def _validate_electrical_topology(
    circuit: PhysicalCircuit,
    routing: wire_routing.RoutingPlan,
    relay_ids: set[int],
) -> tuple[
    dict[int, tuple[abstract.Endpoint, ...]],
    dict[int, WireColor],
    dict[int, frozenset[int]],
]:
    known_ids = {entity.id for entity in circuit.entities} | relay_ids
    entities = {entity.id: entity for entity in circuit.entities}
    expected = _DisjointSet()
    expected_endpoints: dict[tuple[int, int], WireEndpoint] = {}
    expected_colors: dict[tuple[int, int], WireColor] = {}
    for connection in circuit.connections:
        source = _physical_node(circuit, connection.source, connection.color)
        target = _physical_node(circuit, connection.target, connection.color)
        expected.union(source, target)
        expected_endpoints[source] = connection.source
        expected_endpoints[target] = connection.target
        expected_colors[source] = connection.color
        expected_colors[target] = connection.color

    roots = sorted({expected.find(node) for node in expected_endpoints})
    group_by_root = {root: index + 1 for index, root in enumerate(roots)}
    expected_group = {node: group_by_root[expected.find(node)] for node in expected_endpoints}
    endpoints_by_group: dict[int, set[abstract.Endpoint]] = defaultdict(set)
    colors_by_group: dict[int, WireColor] = {}
    for node, endpoint in expected_endpoints.items():
        group = expected_group[node]
        endpoints_by_group[group].add(
            abstract.Endpoint(endpoint.entity, abstract.Connector(endpoint.connector.value))
        )
        color = expected_colors[node]
        previous = colors_by_group.setdefault(group, color)
        if previous is not color:
            raise ValueError(f"expected physical net group {group} mixes red and green")

    actual = _DisjointSet()
    actual_nodes: set[tuple[int, int]] = set()
    wire_keys: set[tuple[int, int, int, int, WireColor]] = set()
    for wire in routing.wires:
        if wire.source_entity not in known_ids or wire.target_entity not in known_ids:
            raise ValueError("routed wire refers to an unknown physical object")
        _validate_connector(entities.get(wire.source_entity), wire.source_connector_id, wire.color)
        _validate_connector(entities.get(wire.target_entity), wire.target_connector_id, wire.color)
        source = (wire.source_entity, wire.source_connector_id)
        target = (wire.target_entity, wire.target_connector_id)
        if source == target:
            raise ValueError("routed wire connects one connector to itself")
        key_left, key_right = sorted((source, target))
        key = (*key_left, *key_right, wire.color)
        if key in wire_keys:
            raise ValueError("physical layout contains a duplicate routed wire")
        wire_keys.add(key)
        actual.union(source, target)
        actual_nodes.update((source, target))

    groups_by_component: dict[tuple[int, int], set[int]] = defaultdict(set)
    for node, group in expected_group.items():
        if node in actual_nodes:
            groups_by_component[actual.find(node)].add(group)
    for groups in groups_by_component.values():
        if len(groups) > 1:
            raise ValueError("routed topology electrically merges distinct physical nets")
    for group in sorted(set(expected_group.values())):
        terminals: list[tuple[int, int]] = []
        for node, candidate_group in expected_group.items():
            if candidate_group == group:
                terminals.append(node)
        components = {actual.find(node) for node in terminals}
        if len(components) != 1:
            raise ValueError(f"physical net group {group} is not electrically connected")
    for node in actual_nodes:
        if node[0] not in relay_ids and node not in expected_group:
            raise ValueError("routed topology uses an implementation connector outside any net")

    relay_memberships: dict[int, set[int]] = defaultdict(set)
    for relay_id in relay_ids:
        for connector_id in (1, 2):
            node = (relay_id, connector_id)
            if node not in actual_nodes:
                continue
            groups = groups_by_component.get(actual.find(node), set())
            relay_memberships[relay_id].update(groups)
    return (
        {group: tuple(sorted(endpoints)) for group, endpoints in endpoints_by_group.items()},
        colors_by_group,
        {relay_id: frozenset(relay_memberships[relay_id]) for relay_id in relay_ids},
    )


def _physical_node(
    circuit: PhysicalCircuit,
    endpoint: WireEndpoint,
    color: WireColor,
) -> tuple[int, int]:
    connector = wire_routing._endpoint_connector_id(circuit, endpoint)
    return (endpoint.entity, wire_routing._colorize_connector(connector, color))


def _validate_connector(
    entity: object | None,
    connector_id: int,
    color: WireColor,
) -> None:
    valid = {1} if color is WireColor.RED else {2}
    if entity is not None and isinstance(entity, (ArithmeticCombinator, DeciderCombinator)):
        valid |= {3} if color is WireColor.RED else {4}
    if connector_id not in valid:
        kind = "relay" if entity is None else type(entity).__name__
        raise ValueError(
            f"{kind} connector {connector_id} is inconsistent with {color.value} wiring"
        )


def _lattice_grid(lattice: LegalPlacementLattice) -> base_placement._GridGeometry:
    unit_sites = tuple(
        position
        for position in lattice.unit_sites
        if not _site_overlaps_forbidden(position, (0.5, 0.5), lattice.forbidden_areas)
    )
    wide_sites = tuple(
        position
        for position in lattice.wide_sites
        if not _site_overlaps_forbidden(position, (1.0, 0.5), lattice.forbidden_areas)
    )
    unit_x = tuple(sorted({x for x, _y in unit_sites}))
    wide_x = tuple(sorted({x for x, _y in wide_sites}))
    y_positions = tuple(sorted({y for _x, y in (*unit_sites, *wide_sites)}))
    points = (*lattice.unit_sites, *lattice.wide_sites)
    bounds = (
        min(x for x, _y in points) - 0.5,
        max(x for x, _y in points) + 0.5,
        min(y for _x, y in points) - 0.5,
        max(y for _x, y in points) + 0.5,
    )
    return base_placement._GridGeometry(
        slots=wide_sites,
        unit_slots=unit_sites,
        bounds=bounds,
        relay_forbidden_areas=lattice.forbidden_areas,
        x_positions=wide_x,
        unit_x_positions=unit_x,
        y_positions=y_positions,
    )


def _site_overlaps_forbidden(
    position: Position,
    half: tuple[float, float],
    forbidden_areas: tuple[RelayForbiddenArea, ...],
) -> bool:
    x, y = position
    half_x, half_y = half
    return any(
        x + half_x > left + _EPSILON
        and x - half_x < right - _EPSILON
        and y + half_y > top + _EPSILON
        and y - half_y < bottom - _EPSILON
        for left, right, top, bottom in forbidden_areas
    )


def _materialize_layout(
    original: Layout,
    state: exact._JointState,
    routing: wire_routing.RoutingPlan,
) -> Layout:
    routing = incremental._synchronize_relay_snapshot(state, routing)
    positions = wire_routing.routed_positions(original.circuit, state.positions, routing)
    return Layout(
        circuit=original.circuit,
        positions=positions,
        relays=tuple(
            LayoutRelay(relay.entity_id, relay.position, relay.description)
            for relay in routing.relays
        ),
        wires=tuple(
            LayoutWire(
                wire.source_entity,
                wire.source_connector_id,
                wire.target_entity,
                wire.target_connector_id,
                wire.color,
            )
            for wire in routing.wires
        ),
        signal_allocation=original.signal_allocation,
        net_colors=original.net_colors,
        net_groups=original.net_groups,
    )


def _distance(left: Position, right: Position) -> float:
    return hypot(left[0] - right[0], left[1] - right[1])


__all__ = [
    "LayoutOptimizationProblem",
    "LayoutOptimizationResult",
    "LegalPlacementLattice",
    "PhysicalLayoutMetrics",
    "optimize_physical_layout",
    "physical_layout_metrics",
    "validate_physical_layout",
]
