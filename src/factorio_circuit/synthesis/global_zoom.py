"""Transactional circuit-generic global contraction of an already-valid physical layout.

A zoom proposal contracts every movable implementation entity and routed relay toward one common
center, then projects the desired coordinates back onto the caller's legal placement lattice with a
deterministic collision legalizer. The existing electrical topology is retained during the move.
Consequently the input layout remains an immediate fail-safe fallback: a snapped proposal is
accepted only after the ordinary physical-layout validator confirms footprints, anchors, electrical
topology, and wire reach.

The transform makes no assumptions about circuit shape, semantic roles, or application identity.
After a valid zoom, the ordinary topology-preserving relay simplifier may remove relays made
redundant by the newly shortened geometry.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, replace
from math import floor, inf

from factorio_circuit.ir.physical import ConstantCombinator
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis import layout_optimizer
from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.layout import Layout, LayoutRelay
from factorio_circuit.synthesis.layout_optimizer import (
    LayoutOptimizationProblem,
    PhysicalLayoutMetrics,
    physical_layout_metrics,
)
from factorio_circuit.synthesis.placement import Position

_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class GlobalZoomResult:
    layout: Layout
    before: PhysicalLayoutMetrics
    after: PhysicalLayoutMetrics
    accepted_scales: tuple[float, ...]
    rejected_scales: tuple[tuple[float, str], ...]


@dataclass(slots=True)
class _BoxOccupancy:
    positions: dict[int, Position]
    half_extents: dict[int, tuple[float, float]]
    buckets: dict[tuple[int, int], set[int]]

    @classmethod
    def empty(cls) -> _BoxOccupancy:
        return cls({}, {}, {})

    @staticmethod
    def _keys(position: Position, half: tuple[float, float]) -> tuple[tuple[int, int], ...]:
        left = floor(position[0] - half[0] + _EPSILON)
        right = floor(position[0] + half[0] - _EPSILON)
        top = floor(position[1] - half[1] + _EPSILON)
        bottom = floor(position[1] + half[1] - _EPSILON)
        return tuple((x, y) for x in range(left, right + 1) for y in range(top, bottom + 1))

    def add(self, object_id: int, position: Position, half: tuple[float, float]) -> None:
        self.positions[object_id] = position
        self.half_extents[object_id] = half
        for key in self._keys(position, half):
            self.buckets.setdefault(key, set()).add(object_id)

    def is_clear(self, position: Position, half: tuple[float, float]) -> bool:
        possible = {
            object_id
            for key in self._keys(position, half)
            for object_id in self.buckets.get(key, ())
        }
        return all(
            not base_placement._boxes_overlap(
                position,
                half,
                self.positions[object_id],
                self.half_extents[object_id],
            )
            for object_id in possible
        )


@dataclass(frozen=True, slots=True)
class _SiteRows:
    ys: tuple[float, ...]
    xs_by_y: dict[float, tuple[float, ...]]

    @classmethod
    def build(cls, sites: tuple[Position, ...]) -> _SiteRows:
        rows: dict[float, list[float]] = {}
        for x, y in sites:
            rows.setdefault(y, []).append(x)
        return cls(
            tuple(sorted(rows)),
            {y: tuple(sorted(xs)) for y, xs in rows.items()},
        )

    def nearest_clear(
        self,
        target: Position,
        half: tuple[float, float],
        occupancy: _BoxOccupancy,
    ) -> Position | None:
        if not self.ys:
            return None
        target_x, target_y = target
        pivot = bisect_left(self.ys, target_y)
        low = pivot - 1
        high = pivot
        best: Position | None = None
        best_distance_sq = inf

        while low >= 0 or high < len(self.ys):
            low_dy = abs(self.ys[low] - target_y) if low >= 0 else inf
            high_dy = abs(self.ys[high] - target_y) if high < len(self.ys) else inf
            if min(low_dy, high_dy) ** 2 > best_distance_sq + _EPSILON:
                break
            if low_dy <= high_dy:
                y = self.ys[low]
                low -= 1
            else:
                y = self.ys[high]
                high += 1

            xs = self.xs_by_y[y]
            x_pivot = bisect_left(xs, target_x)
            left = x_pivot - 1
            right = x_pivot
            dy_sq = (y - target_y) ** 2
            while left >= 0 or right < len(xs):
                left_dx = abs(xs[left] - target_x) if left >= 0 else inf
                right_dx = abs(xs[right] - target_x) if right < len(xs) else inf
                dx = min(left_dx, right_dx)
                if dx * dx + dy_sq > best_distance_sq + _EPSILON:
                    break
                if left_dx <= right_dx:
                    x = xs[left]
                    left -= 1
                else:
                    x = xs[right]
                    right += 1
                candidate = (x, y)
                distance_sq = (x - target_x) ** 2 + dy_sq
                is_better = distance_sq < best_distance_sq - _EPSILON or (
                    abs(distance_sq - best_distance_sq) <= _EPSILON
                    and (best is None or candidate < best)
                )
                if occupancy.is_clear(candidate, half) and is_better:
                    best = candidate
                    best_distance_sq = distance_sq
        return best


def _half_extents(problem: LayoutOptimizationProblem) -> dict[int, tuple[float, float]]:
    relay_ids = {relay.entity_id for relay in problem.layout.relays}
    entities = {entity.id: entity for entity in problem.layout.circuit.entities}
    return {
        object_id: (
            (0.5, 0.5)
            if object_id in relay_ids
            else base_placement._entity_half_extent(entities[object_id])
        )
        for object_id in problem.layout.positions
    }


def _zoom_center(problem: LayoutOptimizationProblem) -> Position:
    points = (
        list(problem.fixed_positions.values())
        if problem.fixed_positions
        else list(problem.layout.positions.values())
    )
    if not points:
        return (0.0, 0.0)
    return (
        sum(x for x, _y in points) / len(points),
        sum(y for _x, y in points) / len(points),
    )


def _project_zoom_positions(
    problem: LayoutOptimizationProblem,
    scale: float,
) -> tuple[dict[int, Position] | None, str | None]:
    if not 0.0 < scale < 1.0:
        raise ValueError("global zoom scale must be in (0, 1)")

    grid = layout_optimizer._lattice_grid(problem.lattice)
    unit_rows = _SiteRows.build(grid.unit_slots)
    wide_rows = _SiteRows.build(grid.slots)
    relay_ids = {relay.entity_id for relay in problem.layout.relays}
    entities = {entity.id: entity for entity in problem.layout.circuit.entities}
    half_extents = _half_extents(problem)
    fixed = set(problem.fixed_positions)
    center = _zoom_center(problem)
    result = dict(problem.fixed_positions)
    occupancy = _BoxOccupancy.empty()
    for object_id in sorted(fixed):
        occupancy.add(object_id, result[object_id], half_extents[object_id])

    movable = sorted(
        (object_id for object_id in problem.layout.positions if object_id not in fixed),
        key=lambda object_id: (
            -(4.0 * half_extents[object_id][0] * half_extents[object_id][1]),
            -(
                (problem.layout.positions[object_id][0] - center[0]) ** 2
                + (problem.layout.positions[object_id][1] - center[1]) ** 2
            ),
            object_id,
        ),
    )
    for object_id in movable:
        original = problem.layout.positions[object_id]
        desired = (
            center[0] + scale * (original[0] - center[0]),
            center[1] + scale * (original[1] - center[1]),
        )
        rows = (
            unit_rows
            if object_id in relay_ids or isinstance(entities[object_id], ConstantCombinator)
            else wide_rows
        )
        position = rows.nearest_clear(desired, half_extents[object_id], occupancy)
        if position is None:
            return None, f"no collision-free legal site exists for physical object {object_id}"
        result[object_id] = position
        occupancy.add(object_id, position, half_extents[object_id])
    return result, None


def _layout_with_positions(
    problem: LayoutOptimizationProblem,
    positions: dict[int, Position],
) -> Layout:
    return replace(
        problem.layout,
        positions=positions,
        relays=tuple(
            LayoutRelay(relay.entity_id, positions[relay.entity_id], relay.description)
            for relay in problem.layout.relays
        ),
    )


def _simplify_valid_layout(problem: LayoutOptimizationProblem) -> Layout:
    embedding = layout_optimizer._validated_embedding(problem)
    state = embedding.state
    topology = embedding.topology
    while True:
        before = len(state.relay_positions)
        topology = incremental._simplify_feasible_topology(state, topology)
        if len(state.relay_positions) == before:
            break
    layout = layout_optimizer._materialize_layout(problem.layout, state, topology.routing)
    layout_optimizer.validate_physical_layout(replace(problem, layout=layout))
    return layout


def try_global_zoom(
    problem: LayoutOptimizationProblem,
    *,
    scale: float,
) -> tuple[Layout | None, str | None]:
    """Try one fail-safe global contraction while retaining the current routed topology."""

    layout_optimizer.validate_physical_layout(problem)
    positions, failure = _project_zoom_positions(problem, scale)
    if positions is None:
        return None, failure
    candidate = _layout_with_positions(problem, positions)
    candidate_problem = replace(problem, layout=candidate)
    try:
        layout_optimizer.validate_physical_layout(candidate_problem)
        candidate = _simplify_valid_layout(candidate_problem)
    except ValueError as exc:
        return None, str(exc)
    return candidate, None


def compact_by_global_zoom(
    problem: LayoutOptimizationProblem,
    *,
    scales: tuple[float, ...] = (0.70, 0.80, 0.90, 0.95, 0.975),
    max_passes: int = 8,
) -> GlobalZoomResult:
    """Repeatedly choose the best valid global contraction until no scale improves the objective."""

    if max_passes <= 0:
        raise ValueError("max_passes must be positive")
    if not scales:
        raise ValueError("at least one global zoom scale is required")
    layout_optimizer.validate_physical_layout(problem)
    before = physical_layout_metrics(problem.layout)
    current_problem = problem
    current_metrics = before
    accepted: list[float] = []
    rejected: list[tuple[float, str]] = []

    for _pass in range(max_passes):
        best_layout: Layout | None = None
        best_metrics = current_metrics
        best_scale: float | None = None
        for scale in scales:
            candidate, failure = try_global_zoom(current_problem, scale=scale)
            if candidate is None:
                rejected.append((scale, failure or "zoom candidate rejected"))
                continue
            metrics = physical_layout_metrics(candidate)
            if metrics.objective < best_metrics.objective:
                best_layout = candidate
                best_metrics = metrics
                best_scale = scale
        if best_layout is None or best_scale is None:
            break
        current_problem = replace(current_problem, layout=best_layout)
        current_metrics = best_metrics
        accepted.append(best_scale)

    return GlobalZoomResult(
        current_problem.layout,
        before,
        current_metrics,
        tuple(accepted),
        tuple(rejected),
    )
