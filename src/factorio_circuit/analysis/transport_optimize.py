"""Observation-aware exact transport and shared scalar bus planning.

This optimizer deliberately starts *after* temporal placement.  The validated production ALAP
schedule remains fixed; :func:`analyze_temporal_alignment` first removes every phase gap that can be
satisfied by same-token reuse or by a permitted fresh Level observation.  Only the residual
:class:`ExactTransportDemand` objects reach this module.

The shared-bus cost matches the electrically isolated prototype rather than the older raw-span
model.  A lane assigned to a shared scalar bus pays:

* one signal-specific one-tick ingress copy;
* one signal-specific one-tick egress copy for every shared tap; and
* a private one-tick branch when a consumer needs ``start + 1``.

The bus itself pays only for the continuous middle ``Each + 0 -> Each`` stages.  A one- or two-tick
transport therefore has no profitable shareable middle and remains private.  Once a lane joins one
continuous bus segment it occupies one abstract signal slot through that segment's end, so capacity
is conservatively the total number of assigned lanes.  Concrete Factorio signal identities remain a
later interference-coloring decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any

from factorio_circuit.ir.semantic import PayloadShape
from factorio_circuit.target.factorio.signals import DEFAULT_VIRTUAL_SIGNAL_POOL

from .temporal_alignment import ExactTransportDemand, TemporalAlignmentAnalysis
from .temporal_hypergraph import TemporalPlacementError


@dataclass(frozen=True, slots=True)
class SharedTransportLane:
    """One unique abstract lane instance assigned to a shared transport carrier."""

    lane_id: int
    producer: int
    label: str
    start_phase: int
    end_phase: int
    tap_phases: tuple[int, ...]

    @property
    def ingress_phase(self) -> int:
        return self.start_phase + 1

    @property
    def trunk_end_phase(self) -> int:
        return self.end_phase - 1

    @property
    def interface_combinators(self) -> int:
        long_taps = sum(phase >= self.start_phase + 2 for phase in self.tap_phases)
        short_private = int(self.start_phase + 1 in self.tap_phases)
        return 1 + long_taps + short_private


@dataclass(frozen=True, slots=True)
class SharedTransportBus:
    index: int
    start_phase: int
    end_phase: int
    lanes: tuple[SharedTransportLane, ...]

    @property
    def middle_stages(self) -> int:
        return self.end_phase - self.start_phase


@dataclass(frozen=True, slots=True)
class TransportOptimizationResult:
    status: str
    buses: tuple[SharedTransportBus, ...]
    private_transports: tuple[ExactTransportDemand, ...]
    bus_middle_stages: int
    bus_interface_combinators: int
    private_scalar_combinators: int
    vector_combinators: int
    objective_combinators: int
    best_bound: int
    wall_time_seconds: float

    @property
    def proven_optimal(self) -> bool:
        return self.status.upper() == "OPTIMAL"


def _load_cp_model() -> Any:
    try:
        return import_module("ortools.sat.python.cp_model")
    except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "exact shared-transport optimization requires OR-Tools for this invocation; run with "
            "`uv run --with 'ortools>=9.14,<10' ...`"
        ) from exc


def _all_private_result(
    transports: tuple[ExactTransportDemand, ...],
    *,
    status: str = "OPTIMAL",
) -> TransportOptimizationResult:
    scalar = sum(item.length for item in transports if item.shape is PayloadShape.SCALAR)
    vector = sum(item.length for item in transports if item.shape is PayloadShape.VECTOR)
    return TransportOptimizationResult(
        status=status,
        buses=(),
        private_transports=transports,
        bus_middle_stages=0,
        bus_interface_combinators=0,
        private_scalar_combinators=scalar,
        vector_combinators=vector,
        objective_combinators=scalar + vector,
        best_bound=scalar + vector,
        wall_time_seconds=0.0,
    )


def optimize_exact_transports(
    analysis: TemporalAlignmentAnalysis,
    *,
    bus_capacity: int = len(DEFAULT_VIRTUAL_SIGNAL_POOL),
    max_buses: int | None = None,
    time_limit_seconds: float = 60.0,
    workers: int = 1,
    incompatible_pairs: tuple[tuple[int, int], ...] = (),
) -> TransportOptimizationResult:
    """Choose private exact chains versus isolated shared scalar buses for a fixed placement.

    Fresh ``OBSERVE_AT`` and same-token ``REUSE`` uses are absent from the optimization because the
    preceding temporal-alignment analysis has already proved them free.  Vector transports remain
    private in this first bus milestone.  Scalar transports with fewer than three ticks also remain
    private because ingress/egress isolation leaves no middle stage to share.
    """

    if isinstance(bus_capacity, bool) or not isinstance(bus_capacity, int) or bus_capacity < 1:
        raise ValueError("bus_capacity must be a positive integer")
    if time_limit_seconds <= 0:
        raise ValueError("time_limit_seconds must be positive")
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")

    transports = analysis.transports
    candidates = tuple(item for item in transports if item.scalar_bus_candidate)
    candidate_ids = {item.producer for item in candidates}
    if len(candidate_ids) != len(candidates):
        raise TemporalPlacementError(
            "shared transport planning currently requires one exact lifetime per producer"
        )

    if max_buses is None:
        max_buses = len(candidates) // 2
    if isinstance(max_buses, bool) or not isinstance(max_buses, int) or max_buses < 0:
        raise ValueError("max_buses must be a non-negative integer or None")
    max_buses = min(max_buses, len(candidates) // 2)

    incompatible: set[tuple[int, int]] = {
        (min(left, right), max(left, right)) for left, right in incompatible_pairs
    }
    if any(left == right for left, right in incompatible):
        raise ValueError("a transport incompatibility pair must contain distinct producers")
    unknown = {item for pair in incompatible for item in pair} - candidate_ids
    if unknown:
        raise ValueError(
            f"transport incompatibility references unknown bus candidates: {sorted(unknown)}"
        )

    # With fewer than two long scalar lifetimes there is no middle stage that can actually be
    # shared.  Return the exact private-prefix cost without requiring the optional solver.
    if len(candidates) < 2 or max_buses == 0 or bus_capacity < 2:
        return _all_private_result(transports)

    cp_model = _load_cp_model()
    model = cp_model.CpModel()

    horizon = max((item.end_phase for item in candidates), default=1) + 1
    assignments: dict[tuple[int, int], Any] = {}
    private_vars: dict[int, Any] = {}

    for candidate_index, item in enumerate(candidates):
        private = model.NewBoolVar(f"private_{item.producer}")
        private_vars[item.producer] = private
        row: list[Any] = []
        for bus in range(max_buses):
            assignment = model.NewBoolVar(f"bus_{bus}_producer_{item.producer}")
            assignments[(item.producer, bus)] = assignment
            row.append(assignment)
            # Deterministic symmetry reduction only: after bus labels are ordered, candidate i can
            # never need a bus index greater than i.  Stronger pairing assumptions can remove real
            # optima when overlapping intervals favor crossed groups.
            if bus > candidate_index:
                model.Add(assignment == 0)
        model.Add(private + sum(row) == 1)

    bus_active: list[Any] = []
    bus_starts: list[Any] = []
    bus_ends: list[Any] = []
    bus_spans: list[Any] = []

    for bus in range(max_buses):
        column = [assignments[(item.producer, bus)] for item in candidates]
        active = model.NewBoolVar(f"bus_{bus}_active")
        model.AddMaxEquality(active, column)
        # One lane cannot save hardware under the isolated ingress/egress cost model.
        model.Add(sum(column) >= 2).OnlyEnforceIf(active)
        model.Add(sum(column) == 0).OnlyEnforceIf(active.Not())
        model.Add(sum(column) <= bus_capacity)
        bus_active.append(active)

        start_candidates: list[Any] = []
        end_candidates: list[Any] = []
        for item in candidates:
            assigned = assignments[(item.producer, bus)]
            start_candidate = model.NewIntVar(0, horizon, f"bus_{bus}_start_{item.producer}")
            end_candidate = model.NewIntVar(0, horizon, f"bus_{bus}_end_{item.producer}")
            model.Add(start_candidate == item.start_phase + 1).OnlyEnforceIf(assigned)
            model.Add(start_candidate == horizon).OnlyEnforceIf(assigned.Not())
            model.Add(end_candidate == item.end_phase - 1).OnlyEnforceIf(assigned)
            model.Add(end_candidate == 0).OnlyEnforceIf(assigned.Not())
            start_candidates.append(start_candidate)
            end_candidates.append(end_candidate)

        start = model.NewIntVar(0, horizon, f"bus_{bus}_start")
        end = model.NewIntVar(0, horizon, f"bus_{bus}_end")
        span = model.NewIntVar(0, horizon, f"bus_{bus}_span")
        model.AddMinEquality(start, start_candidates)
        model.AddMaxEquality(end, end_candidates)
        model.Add(span == end - start).OnlyEnforceIf(active)
        model.Add(span == 0).OnlyEnforceIf(active.Not())
        bus_starts.append(start)
        bus_ends.append(end)
        bus_spans.append(span)

        for left, right in incompatible:
            model.Add(assignments[(left, bus)] + assignments[(right, bus)] <= 1)

    for bus in range(max(0, max_buses - 1)):
        model.Add(bus_active[bus] >= bus_active[bus + 1])

    fixed_scalar = sum(
        item.length
        for item in transports
        if item.shape is PayloadShape.SCALAR and item.producer not in candidate_ids
    )
    fixed_vector = sum(
        item.length for item in transports if item.shape is PayloadShape.VECTOR
    )

    private_terms = [item.length * private_vars[item.producer] for item in candidates]
    interface_terms = [
        (1 + len(item.long_tap_phases) + int(item.has_one_tick_tap))
        * assignments[(item.producer, bus)]
        for item in candidates
        for bus in range(max_buses)
    ]
    model.Minimize(sum([*bus_spans, *private_terms, *interface_terms]))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_seconds)
    solver.parameters.num_search_workers = workers
    status_code = solver.Solve(model)
    status = str(solver.StatusName(status_code))
    if status.upper() not in {"OPTIMAL", "FEASIBLE"}:
        raise TemporalPlacementError(f"transport CP-SAT search finished with status {status}")

    buses: list[SharedTransportBus] = []
    assigned_to_bus: set[int] = set()
    next_lane_id = 1
    for bus in range(max_buses):
        if not solver.BooleanValue(bus_active[bus]):
            continue
        lanes: list[SharedTransportLane] = []
        for item in candidates:
            if solver.BooleanValue(assignments[(item.producer, bus)]):
                assigned_to_bus.add(item.producer)
                lanes.append(
                    SharedTransportLane(
                        lane_id=next_lane_id,
                        producer=item.producer,
                        label=item.label,
                        start_phase=item.start_phase,
                        end_phase=item.end_phase,
                        tap_phases=item.tap_phases,
                    )
                )
                next_lane_id += 1
        buses.append(
            SharedTransportBus(
                index=bus,
                start_phase=int(solver.Value(bus_starts[bus])),
                end_phase=int(solver.Value(bus_ends[bus])),
                lanes=tuple(
                    sorted(
                        lanes,
                        key=lambda lane: (lane.start_phase, lane.end_phase, lane.producer),
                    )
                ),
            )
        )

    private = tuple(item for item in transports if item.producer not in assigned_to_bus)
    bus_middle = sum(item.middle_stages for item in buses)
    bus_interfaces = sum(lane.interface_combinators for bus in buses for lane in bus.lanes)
    private_scalar = sum(
        item.length for item in private if item.shape is PayloadShape.SCALAR
    )
    vector = sum(item.length for item in private if item.shape is PayloadShape.VECTOR)
    objective = bus_middle + bus_interfaces + private_scalar + vector
    fixed = fixed_scalar + fixed_vector
    variable_bound = int(round(solver.BestObjectiveBound()))

    return TransportOptimizationResult(
        status=status,
        buses=tuple(sorted(buses, key=lambda item: (item.start_phase, item.end_phase, item.index))),
        private_transports=private,
        bus_middle_stages=bus_middle,
        bus_interface_combinators=bus_interfaces,
        private_scalar_combinators=private_scalar,
        vector_combinators=vector,
        objective_combinators=objective,
        best_bound=variable_bound + fixed,
        wall_time_seconds=float(solver.WallTime()),
    )


def format_transport_optimization(result: TransportOptimizationResult) -> str:
    """Render a compact fixed-placement transport report."""

    lines = [
        "observation-aware exact transport optimization",
        (
            f"  status={result.status}; model_optimal={result.proven_optimal}; "
            f"objective={result.objective_combinators}; best_bound={result.best_bound}; "
            f"wall={result.wall_time_seconds:.3f}s"
        ),
        (
            f"  bus_middle={result.bus_middle_stages}; "
            f"bus_interfaces={result.bus_interface_combinators}; "
            f"private_scalar={result.private_scalar_combinators}; "
            f"vector={result.vector_combinators}; buses={len(result.buses)}"
        ),
    ]
    for bus in result.buses:
        lines.append(
            f"    bus {bus.index}: middle=[{bus.start_phase}, {bus.end_phase}) "
            f"stages={bus.middle_stages}; lanes={len(bus.lanes)}"
        )
    return "\n".join(lines)


__all__ = [
    "SharedTransportBus",
    "SharedTransportLane",
    "TransportOptimizationResult",
    "format_transport_optimization",
    "optimize_exact_transports",
]
