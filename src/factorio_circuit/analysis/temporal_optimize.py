"""Exact temporal placement and scalar delay-bus packing with optional OR-Tools CP-SAT.

This module is intentionally outside the mandatory compiler dependency set.  The temporal
hypergraph and cost model are pure Python; callers that want a proof-producing global search can
install the ``solver`` extra and invoke :func:`optimize_temporal_hypergraph`.

A scalar delay bus is modeled conservatively as one continuous ``Each + 0 -> Each`` pipeline.  A
lane may join after the bus begins, but once assigned it remains present through the bus end.  Thus a
bus containing value lifetimes ``[s_i, e_i)`` costs ``max(e_i) - min(s_i)`` physical stages, not the
size of the union of those intervals.  This matches a realizable Factorio bus where arbitrary lanes
cannot disappear between two Each stages without additional filtering hardware.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any

from factorio_circuit.ir.semantic import PayloadShape
from factorio_circuit.target.factorio.signals import DEFAULT_VIRTUAL_SIGNAL_POOL

from .temporal_hypergraph import (
    TemporalHypergraph,
    TemporalPlacement,
    TemporalPlacementError,
)


@dataclass(frozen=True, slots=True)
class DelayBusLane:
    producer: int
    label: str
    start_phase: int
    end_phase: int


@dataclass(frozen=True, slots=True)
class DelayBusPlan:
    index: int
    start_phase: int
    end_phase: int
    lanes: tuple[DelayBusLane, ...]

    @property
    def stages(self) -> int:
        return self.end_phase - self.start_phase


@dataclass(frozen=True, slots=True)
class TemporalOptimizationResult:
    status: str
    placement: TemporalPlacement
    buses: tuple[DelayBusPlan, ...]
    bus_stages: int
    ordinary_scalar_delays: int
    vector_delays: int
    objective_delays: int
    best_bound: int
    wall_time_seconds: float

    @property
    def proven_optimal(self) -> bool:
        return self.status.upper() == "OPTIMAL"


def _load_cp_model() -> Any:
    try:
        return import_module("ortools.sat.python.cp_model")
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional environment
        raise RuntimeError(
            "exact temporal optimization requires the optional solver dependency; "
            "install it with `uv sync --extra solver` or run with `uv run --extra solver ...`"
        ) from exc


def optimize_temporal_hypergraph(
    graph: TemporalHypergraph,
    *,
    bus_capacity: int = len(DEFAULT_VIRTUAL_SIGNAL_POOL),
    max_buses: int | None = None,
    time_limit_seconds: float = 60.0,
    workers: int = 1,
    incompatible_pairs: tuple[tuple[int, int], ...] = (),
) -> TemporalOptimizationResult:
    """Minimize total delay combinators for one fixed-period hypergraph.

    Computation phases are integer decision variables constrained to their precomputed mobility
    windows.  Eligible scalar values are partitioned among continuous delay buses.  Non-bus scalar
    values and whole vectors retain ordinary one-value-per-stage transport cost.  Computation/state
    entity counts are fixed in this first solver, so minimizing delay stages is exactly the relevant
    variable part of the combinator count.

    ``incompatible_pairs`` is a forward-compatible hook for physical lane-interference analysis:
    two listed producer ids may not occupy the same delay bus.
    """

    if isinstance(bus_capacity, bool) or not isinstance(bus_capacity, int) or bus_capacity < 1:
        raise ValueError("bus_capacity must be a positive integer")
    if time_limit_seconds <= 0:
        raise ValueError("time_limit_seconds must be positive")
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")

    cp_model = _load_cp_model()
    model = cp_model.CpModel()

    computations = {item.id: item for item in graph.computations}
    sources = {item.id: item for item in graph.sources}
    sinks = {item.id: item for item in graph.sinks}
    outgoing: dict[int, list[object]] = {}
    for arc in graph.arcs:
        outgoing.setdefault(arc.producer, []).append(arc)

    horizon = max(
        [graph.period, *(item.latest_phase + 1 for item in graph.computations), *(item.phase + 1 for item in graph.sinks)],
        default=1,
    )

    phases: dict[int, Any] = {}
    for item in graph.computations:
        phases[item.id] = model.NewIntVar(
            item.earliest_phase,
            item.latest_phase,
            f"phase_{item.id}_{item.label}",
        )

    # Re-state dependency constraints in the solver even though the mobility windows already encode
    # their transitive bounds.  This both documents the model and protects future graph builders that
    # may compute looser independent windows.
    for arc in graph.arcs:
        if arc.consumer in computations:
            consumer_phase = phases[arc.consumer]
            if arc.producer in computations:
                model.Add(phases[arc.producer] + arc.latency <= consumer_phase)
            else:
                model.Add(sources[arc.producer].start_phase + arc.latency <= consumer_phase)
        else:
            sink_phase = sinks[arc.consumer].phase
            if arc.producer in computations:
                model.Add(phases[arc.producer] <= sink_phase)
            elif sources[arc.producer].start_phase > sink_phase:
                raise TemporalPlacementError(
                    f"source {sources[arc.producer].label!r} starts after sink phase {sink_phase}"
                )

    consumer_inputs: dict[tuple[int, int, int], Any] = {}
    for arc in graph.arcs:
        key = (arc.producer, arc.consumer, arc.latency)
        if key in consumer_inputs:
            continue
        if arc.consumer in computations:
            value = model.NewIntVar(0, horizon, f"input_{arc.producer}_{arc.consumer}_{arc.latency}")
            model.Add(value == phases[arc.consumer] - arc.latency)
        else:
            value = model.NewConstant(sinks[arc.consumer].phase)
        consumer_inputs[key] = value

    end_vars: dict[int, Any] = {}
    for producer, arcs in outgoing.items():
        if producer not in computations and producer not in sources:
            continue
        terms = [consumer_inputs[(arc.producer, arc.consumer, arc.latency)] for arc in arcs]
        if not terms:
            continue
        end = model.NewIntVar(0, horizon, f"end_{producer}")
        model.AddMaxEquality(end, terms)
        end_vars[producer] = end

    bus_candidates = [item for item in graph.computations if item.delay_bus_eligible]
    candidate_ids = {item.id for item in bus_candidates}
    if max_buses is None:
        max_buses = len(bus_candidates)
    if isinstance(max_buses, bool) or not isinstance(max_buses, int) or max_buses < 0:
        raise ValueError("max_buses must be a non-negative integer or None")
    max_buses = min(max_buses, len(bus_candidates))
    if bus_candidates and max_buses == 0:
        raise ValueError("max_buses=0 cannot carry delay-bus-eligible values")

    delayed: dict[int, Any] = {}
    assignments: dict[tuple[int, int], Any] = {}
    for candidate_index, item in enumerate(bus_candidates):
        end = end_vars[item.id]
        flag = model.NewBoolVar(f"delayed_{item.id}")
        model.Add(end >= phases[item.id] + 1).OnlyEnforceIf(flag)
        model.Add(end == phases[item.id]).OnlyEnforceIf(flag.Not())
        delayed[item.id] = flag

        row: list[Any] = []
        for bus in range(max_buses):
            assignment = model.NewBoolVar(f"bus_{bus}_value_{item.id}")
            assignments[(item.id, bus)] = assignment
            row.append(assignment)
            # Standard unlabeled-bin symmetry break: the i-th value never needs a bus with index
            # greater than i because earlier empty labels are interchangeable.
            if bus > candidate_index:
                model.Add(assignment == 0)
        model.Add(sum(row) == flag)

    incompatible = {tuple(sorted(pair)) for pair in incompatible_pairs}
    if any(left == right for left, right in incompatible):
        raise ValueError("a delay-bus incompatibility pair must contain two distinct producers")
    unknown = {item for pair in incompatible for item in pair} - candidate_ids
    if unknown:
        raise ValueError(f"delay-bus incompatibility references unknown candidates: {sorted(unknown)}")

    bus_active: list[Any] = []
    bus_starts: list[Any] = []
    bus_ends: list[Any] = []
    bus_spans: list[Any] = []
    for bus in range(max_buses):
        column = [assignments[(item.id, bus)] for item in bus_candidates]
        active = model.NewBoolVar(f"bus_{bus}_active")
        if column:
            model.AddMaxEquality(active, column)
        else:  # pragma: no cover - max_buses is zero when there are no candidates
            model.Add(active == 0)
        bus_active.append(active)

        start_candidates: list[Any] = []
        end_candidates: list[Any] = []
        for item in bus_candidates:
            assignment = assignments[(item.id, bus)]
            start_candidate = model.NewIntVar(0, horizon, f"bus_{bus}_start_value_{item.id}")
            end_candidate = model.NewIntVar(0, horizon, f"bus_{bus}_end_value_{item.id}")
            model.Add(start_candidate == phases[item.id]).OnlyEnforceIf(assignment)
            model.Add(start_candidate == horizon).OnlyEnforceIf(assignment.Not())
            model.Add(end_candidate == end_vars[item.id]).OnlyEnforceIf(assignment)
            model.Add(end_candidate == 0).OnlyEnforceIf(assignment.Not())
            start_candidates.append(start_candidate)
            end_candidates.append(end_candidate)

        start = model.NewIntVar(0, horizon, f"bus_{bus}_start")
        end = model.NewIntVar(0, horizon, f"bus_{bus}_end")
        span = model.NewIntVar(0, horizon, f"bus_{bus}_span")
        if start_candidates:
            model.AddMinEquality(start, start_candidates)
            model.AddMaxEquality(end, end_candidates)
        else:  # pragma: no cover
            model.Add(start == horizon)
            model.Add(end == 0)
        model.Add(span == end - start).OnlyEnforceIf(active)
        model.Add(span == 0).OnlyEnforceIf(active.Not())
        bus_starts.append(start)
        bus_ends.append(end)
        bus_spans.append(span)

        model.Add(sum(column) <= bus_capacity)
        for left, right in incompatible:
            model.Add(assignments[(left, bus)] + assignments[(right, bus)] <= 1)

    # Pack active buses toward low indices to eliminate the factorial label symmetry.
    for bus in range(max(0, max_buses - 1)):
        model.Add(bus_active[bus] >= bus_active[bus + 1])

    ordinary_scalar_terms: list[Any] = []
    vector_terms: list[Any] = []

    for item in graph.computations:
        if item.id in candidate_ids:
            continue
        end = end_vars.get(item.id)
        if end is None:
            continue
        length = model.NewIntVar(0, horizon, f"ordinary_length_{item.id}")
        model.Add(length == end - phases[item.id])
        if item.shape is PayloadShape.SCALAR:
            ordinary_scalar_terms.append(length)
        else:
            vector_terms.append(length)

    # Exact leaves retain the historical exact-transport cost.  LIVE/STABLE leaves can be observed
    # directly at their consumer tick inside the current occurrence and therefore add no transport
    # objective here.
    for source in graph.sources:
        if source.mode.value != "exact":
            continue
        end = end_vars.get(source.id)
        if end is None:
            continue
        length = model.NewIntVar(0, horizon, f"source_length_{source.id}")
        model.Add(length == end - source.start_phase)
        if source.shape is PayloadShape.SCALAR:
            ordinary_scalar_terms.append(length)
        else:
            vector_terms.append(length)

    objective_terms = [*bus_spans, *ordinary_scalar_terms, *vector_terms]
    model.Minimize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_seconds)
    solver.parameters.num_search_workers = workers
    status_code = solver.Solve(model)
    status = str(solver.StatusName(status_code))
    if status.upper() not in {"OPTIMAL", "FEASIBLE"}:
        raise TemporalPlacementError(f"temporal CP-SAT search finished with status {status}")

    placement = TemporalPlacement(
        tuple((item.id, int(solver.Value(phases[item.id]))) for item in graph.computations)
    )
    graph.validate_placement(placement)

    buses: list[DelayBusPlan] = []
    for bus in range(max_buses):
        if not solver.BooleanValue(bus_active[bus]):
            continue
        lanes: list[DelayBusLane] = []
        for item in bus_candidates:
            if solver.BooleanValue(assignments[(item.id, bus)]):
                lanes.append(
                    DelayBusLane(
                        producer=item.id,
                        label=item.label,
                        start_phase=int(solver.Value(phases[item.id])),
                        end_phase=int(solver.Value(end_vars[item.id])),
                    )
                )
        buses.append(
            DelayBusPlan(
                index=bus,
                start_phase=int(solver.Value(bus_starts[bus])),
                end_phase=int(solver.Value(bus_ends[bus])),
                lanes=tuple(sorted(lanes, key=lambda lane: (lane.start_phase, lane.end_phase, lane.producer))),
            )
        )

    ordinary_scalar = sum(int(solver.Value(item)) for item in ordinary_scalar_terms)
    vector = sum(int(solver.Value(item)) for item in vector_terms)
    bus_stage_count = sum(item.stages for item in buses)
    objective = bus_stage_count + ordinary_scalar + vector
    return TemporalOptimizationResult(
        status=status,
        placement=placement,
        buses=tuple(sorted(buses, key=lambda item: (item.start_phase, item.end_phase, item.index))),
        bus_stages=bus_stage_count,
        ordinary_scalar_delays=ordinary_scalar,
        vector_delays=vector,
        objective_delays=objective,
        best_bound=int(round(solver.BestObjectiveBound())),
        wall_time_seconds=float(solver.WallTime()),
    )


def format_temporal_optimization(result: TemporalOptimizationResult, *, top_buses: int = 20) -> str:
    """Render a compact exact-search report."""

    lines = [
        "temporal placement optimization",
        (
            f"  status={result.status}; proven_optimal={result.proven_optimal}; "
            f"objective_delays={result.objective_delays}; best_bound={result.best_bound}; "
            f"wall={result.wall_time_seconds:.3f}s"
        ),
        (
            f"  delay cost: buses={result.bus_stages}; ordinary_scalar={result.ordinary_scalar_delays}; "
            f"vector={result.vector_delays}; bus_count={len(result.buses)}"
        ),
        f"  buses (top {min(top_buses, len(result.buses))} by stage count):",
    ]
    ordered = sorted(result.buses, key=lambda item: (-item.stages, item.start_phase, item.index))
    if not ordered:
        lines.append("    (none)")
        return "\n".join(lines)
    for bus in ordered[:top_buses]:
        lines.append(
            f"    bus {bus.index}: [{bus.start_phase}, {bus.end_phase}) "
            f"stages={bus.stages}; lanes={len(bus.lanes)}"
        )
    return "\n".join(lines)


__all__ = [
    "DelayBusLane",
    "DelayBusPlan",
    "TemporalOptimizationResult",
    "format_temporal_optimization",
    "optimize_temporal_hypergraph",
]
