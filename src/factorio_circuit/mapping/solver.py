"""Joint candidate selection and physical-phase optimization for temporal technology mapping.

The solver owns target timing: semantic operations do not arrive with precomputed physical latency
windows. Each selected implementation candidate contributes its own input/output phase equations.
The current milestone supports finite local candidates, free source reuse/observation, prefix-shared
private exact transport, a conservative zero-delay wire-sum candidate, and an optional shared scalar
delay-bus resource whose membership and span are solved together with candidate phases.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any

from factorio_circuit.ir.semantic import PayloadShape

from .plan import (
    DelayBusLane,
    DelayBusResource,
    DeliveryKind,
    ExactLifetime,
    PlannedDelivery,
    RealizationPlan,
    SelectedRealization,
    WireSumResource,
)
from .problem import MappingProblem, MappingProblemError, MappingUse
from .templates import (
    CandidateOutputMode,
    ImplementationCandidate,
    ImplementationKind,
    ordinary_candidates,
)
from .validate import source_delivery_kind, transport_anchor, validate_realization_plan


@dataclass(frozen=True, slots=True)
class MappingOptimizationResult:
    status: str
    plan: RealizationPlan
    wall_time_seconds: float

    @property
    def proven_optimal(self) -> bool:
        return self.status.upper() == "OPTIMAL"


@dataclass(frozen=True, slots=True)
class _LifetimeModel:
    producer: int
    shape: PayloadShape
    start: Any
    end: Any
    length: Any
    uses: tuple[MappingUse, ...]


@dataclass(frozen=True, slots=True)
class _DelayBusModel:
    assignments: dict[tuple[int, int], Any]
    active: tuple[Any, ...]
    starts: tuple[Any, ...]
    ends: tuple[Any, ...]
    lifetimes: dict[int, _LifetimeModel]
    transport_needed: dict[MappingUse, Any]


def _load_cp_model() -> Any:
    try:
        return import_module("ortools.sat.python.cp_model")
    except ModuleNotFoundError as exc:  # pragma: no cover - optional environment
        raise RuntimeError(
            "temporal technology mapping requires OR-Tools for this invocation; run with "
            "`uv run --with 'ortools>=9.14,<10' ...`"
        ) from exc


def solve_mapping_problem(
    problem: MappingProblem,
    *,
    candidates: tuple[ImplementationCandidate, ...] | None = None,
    max_delay_buses: int = 0,
    delay_bus_capacity: int = 256,
    time_limit_seconds: float = 30.0,
    workers: int = 1,
) -> MappingOptimizationResult:
    """Choose implementations, phases, and optional shared delay buses in one CP-SAT model.

    ``max_delay_buses=0`` preserves the original private-prefix objective. When buses are enabled,
    scalar exact lifetimes of at least three ticks may be assigned to one isolated shared bus. Bus
    membership, bus span, and computation phases are all variables in the same solve; there is no
    fixed-placement transport optimization pass in front of this model.
    """

    if time_limit_seconds <= 0:
        raise ValueError("time_limit_seconds must be positive")
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")
    if isinstance(max_delay_buses, bool) or not isinstance(max_delay_buses, int):
        raise ValueError("max_delay_buses must be a non-negative integer")
    if max_delay_buses < 0:
        raise ValueError("max_delay_buses must be a non-negative integer")
    if isinstance(delay_bus_capacity, bool) or not isinstance(delay_bus_capacity, int):
        raise ValueError("delay_bus_capacity must be a positive integer")
    if delay_bus_capacity < 1:
        raise ValueError("delay_bus_capacity must be a positive integer")

    selected_candidates = candidates if candidates is not None else ordinary_candidates(problem)
    _validate_candidate_set(problem, selected_candidates)
    if any(item.output_mode is not CandidateOutputMode.EXACT for item in selected_candidates):
        raise MappingProblemError(
            "the first joint mapper supports EXACT candidate outputs only; source observation "
            "windows remain supported independently"
        )

    cp_model = _load_cp_model()
    model = cp_model.CpModel()
    operations = {item.id: item for item in problem.operations}
    sources = {item.id: item for item in problem.sources}
    sinks = {item.id: item for item in problem.sinks}
    candidates_by_operation = _candidates_by_operation(problem, selected_candidates)

    choose: dict[int, Any] = {}
    output_phase: dict[int, Any] = {}
    use_phase: dict[MappingUse, Any] = {}

    for operation in problem.operations:
        output_phase[operation.id] = model.NewIntVar(
            0,
            problem.horizon,
            f"mapping_output_phase_{operation.id}",
        )
        choices = []
        for candidate in candidates_by_operation[operation.id]:
            variable = model.NewBoolVar(f"mapping_candidate_{candidate.id}")
            choose[candidate.id] = variable
            choices.append(variable)
        model.Add(sum(choices) == 1)

    for use in problem.uses():
        if use.operand_index is None:
            use_phase[use] = model.NewConstant(sinks[use.consumer].phase)
        else:
            use_phase[use] = model.NewIntVar(
                0,
                problem.horizon,
                f"mapping_use_{use.producer}_{use.consumer}_{use.operand_index}",
            )

    for operation in problem.operations:
        for candidate in candidates_by_operation[operation.id]:
            for operand_index, offset in enumerate(candidate.input_phase_offsets):
                use = MappingUse(operation.operands[operand_index], operation.id, operand_index)
                constraint = use_phase[use] == output_phase[operation.id] + offset
                model.Add(constraint).OnlyEnforceIf(choose[candidate.id])

            if candidate.kind is ImplementationKind.WIRE_SUM:
                for producer in operation.operands:
                    if producer not in operations:
                        raise MappingProblemError(
                            "wire-sum candidate requires operation-result operands"
                        )
                    model.Add(output_phase[producer] == output_phase[operation.id]).OnlyEnforceIf(
                        choose[candidate.id]
                    )

    outgoing: dict[int, list[MappingUse]] = {item: [] for item in problem.value_ids}
    for use in problem.uses():
        phase = use_phase[use]
        outgoing[use.producer].append(use)
        if use.producer in operations:
            model.Add(phase >= output_phase[use.producer])
        else:
            model.Add(phase >= sources[use.producer].start_phase)

    lifetimes = _build_lifetime_models(
        model,
        problem,
        output_phase,
        use_phase,
        outgoing,
    )
    bus_model, transport_terms = _add_delay_bus_model(
        model,
        problem,
        lifetimes,
        use_phase,
        max_delay_buses=max_delay_buses,
        delay_bus_capacity=delay_bus_capacity,
    )

    entity_terms = [
        candidate.entity_cost * choose[candidate.id] for candidate in selected_candidates
    ]
    model.Minimize(sum(entity_terms) + sum(transport_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_seconds)
    solver.parameters.num_search_workers = workers
    status_code = solver.Solve(model)
    status = str(solver.StatusName(status_code))
    if status.upper() not in {"OPTIMAL", "FEASIBLE"}:
        raise MappingProblemError(f"temporal technology mapping failed with status {status}")

    plan = _extract_plan(
        problem,
        candidates_by_operation,
        choose,
        output_phase,
        use_phase,
        bus_model,
        solver,
    )
    validate_realization_plan(problem, selected_candidates, plan)
    return MappingOptimizationResult(
        status=status,
        plan=plan,
        wall_time_seconds=float(solver.WallTime()),
    )


def _build_lifetime_models(
    model: Any,
    problem: MappingProblem,
    output_phase: dict[int, Any],
    use_phase: dict[MappingUse, Any],
    outgoing: dict[int, list[MappingUse]],
) -> dict[int, _LifetimeModel]:
    lifetimes: dict[int, _LifetimeModel] = {}

    for operation in problem.operations:
        uses = tuple(outgoing[operation.id])
        end = model.NewIntVar(0, problem.horizon, f"mapping_exact_end_{operation.id}")
        model.AddMaxEquality(
            end,
            [output_phase[operation.id], *(use_phase[use] for use in uses)],
        )
        length = model.NewIntVar(0, problem.horizon, f"mapping_exact_length_{operation.id}")
        model.Add(length == end - output_phase[operation.id])
        lifetimes[operation.id] = _LifetimeModel(
            producer=operation.id,
            shape=operation.shape,
            start=output_phase[operation.id],
            end=end,
            length=length,
            uses=uses,
        )

    for source in problem.sources:
        anchor = transport_anchor(source)
        uses = tuple(outgoing[source.id])
        if anchor is None or not uses:
            continue
        end = model.NewIntVar(anchor, problem.horizon, f"mapping_source_end_{source.id}")
        model.AddMaxEquality(end, [model.NewConstant(anchor), *(use_phase[use] for use in uses)])
        length = model.NewIntVar(0, problem.horizon, f"mapping_source_length_{source.id}")
        model.Add(length == end - anchor)
        lifetimes[source.id] = _LifetimeModel(
            producer=source.id,
            shape=source.shape,
            start=model.NewConstant(anchor),
            end=end,
            length=length,
            uses=uses,
        )

    return lifetimes


def _add_delay_bus_model(
    model: Any,
    problem: MappingProblem,
    lifetimes: dict[int, _LifetimeModel],
    use_phase: dict[MappingUse, Any],
    *,
    max_delay_buses: int,
    delay_bus_capacity: int,
) -> tuple[_DelayBusModel | None, list[Any]]:
    scalar = tuple(
        lifetime for lifetime in lifetimes.values() if lifetime.shape is PayloadShape.SCALAR
    )
    bus_count = min(max_delay_buses, len(scalar) // 2)
    if bus_count == 0:
        return None, [lifetime.length for lifetime in lifetimes.values()]

    transport_needed: dict[MappingUse, Any] = {}
    for lifetime in lifetimes.values():
        for use in lifetime.uses:
            needed = model.NewBoolVar(
                f"mapping_transport_needed_{use.producer}_{use.consumer}_{use.operand_index}"
            )
            model.Add(use_phase[use] >= lifetime.start + 1).OnlyEnforceIf(needed)
            model.Add(use_phase[use] <= lifetime.start).OnlyEnforceIf(needed.Not())
            transport_needed[use] = needed

    assignments: dict[tuple[int, int], Any] = {}
    private_costs: list[Any] = []
    interface_terms: list[Any] = []

    for lifetime in scalar:
        private = model.NewBoolVar(f"mapping_private_{lifetime.producer}")
        row = []
        for bus in range(bus_count):
            assigned = model.NewBoolVar(f"mapping_bus_{bus}_producer_{lifetime.producer}")
            assignments[(lifetime.producer, bus)] = assigned
            row.append(assigned)
            model.Add(lifetime.length >= 3).OnlyEnforceIf(assigned)
            interface_terms.append(assigned)
            for use in lifetime.uses:
                both = model.NewBoolVar(
                    f"mapping_bus_{bus}_use_{use.producer}_{use.consumer}_{use.operand_index}"
                )
                model.AddMultiplicationEquality(
                    both,
                    [assigned, transport_needed[use]],
                )
                interface_terms.append(both)
        model.Add(private + sum(row) == 1)
        private_cost = model.NewIntVar(
            0,
            problem.horizon,
            f"mapping_private_cost_{lifetime.producer}",
        )
        model.Add(private_cost == lifetime.length).OnlyEnforceIf(private)
        model.Add(private_cost == 0).OnlyEnforceIf(private.Not())
        private_costs.append(private_cost)

    active_vars: list[Any] = []
    start_vars: list[Any] = []
    end_vars: list[Any] = []
    span_vars: list[Any] = []
    sentinel = problem.horizon + 1

    for bus in range(bus_count):
        column = [assignments[(lifetime.producer, bus)] for lifetime in scalar]
        active = model.NewBoolVar(f"mapping_bus_{bus}_active")
        model.AddMaxEquality(active, column)
        model.Add(sum(column) >= 2).OnlyEnforceIf(active)
        model.Add(sum(column) == 0).OnlyEnforceIf(active.Not())
        model.Add(sum(column) <= delay_bus_capacity)
        active_vars.append(active)

        start_candidates = []
        end_candidates = []
        for lifetime in scalar:
            assigned = assignments[(lifetime.producer, bus)]
            start_candidate = model.NewIntVar(
                0,
                sentinel,
                f"mapping_bus_{bus}_start_{lifetime.producer}",
            )
            end_candidate = model.NewIntVar(
                0,
                problem.horizon,
                f"mapping_bus_{bus}_end_{lifetime.producer}",
            )
            model.Add(start_candidate == lifetime.start + 1).OnlyEnforceIf(assigned)
            model.Add(start_candidate == sentinel).OnlyEnforceIf(assigned.Not())
            model.Add(end_candidate == lifetime.end - 1).OnlyEnforceIf(assigned)
            model.Add(end_candidate == 0).OnlyEnforceIf(assigned.Not())
            start_candidates.append(start_candidate)
            end_candidates.append(end_candidate)

        start = model.NewIntVar(0, sentinel, f"mapping_bus_{bus}_start")
        end = model.NewIntVar(0, problem.horizon, f"mapping_bus_{bus}_end")
        span = model.NewIntVar(0, problem.horizon, f"mapping_bus_{bus}_span")
        model.AddMinEquality(start, start_candidates)
        model.AddMaxEquality(end, end_candidates)
        model.Add(span == end - start).OnlyEnforceIf(active)
        model.Add(span == 0).OnlyEnforceIf(active.Not())
        start_vars.append(start)
        end_vars.append(end)
        span_vars.append(span)

    for bus in range(bus_count - 1):
        model.Add(active_vars[bus] >= active_vars[bus + 1])

    non_scalar_terms = [
        lifetime.length
        for lifetime in lifetimes.values()
        if lifetime.shape is not PayloadShape.SCALAR
    ]
    model_data = _DelayBusModel(
        assignments=assignments,
        active=tuple(active_vars),
        starts=tuple(start_vars),
        ends=tuple(end_vars),
        lifetimes=lifetimes,
        transport_needed=transport_needed,
    )
    return model_data, [*private_costs, *interface_terms, *span_vars, *non_scalar_terms]


def _validate_candidate_set(
    problem: MappingProblem,
    candidates: tuple[ImplementationCandidate, ...],
) -> None:
    if not candidates and problem.operations:
        raise MappingProblemError("mapping problem has operations but no implementation candidates")
    candidate_ids = [item.id for item in candidates]
    if len(set(candidate_ids)) != len(candidate_ids):
        raise MappingProblemError("mapping candidate ids must be unique")

    operations = {item.id: item for item in problem.operations}
    covered: set[int] = set()
    for candidate in candidates:
        operation = operations.get(candidate.operation)
        if operation is None:
            raise MappingProblemError(
                f"candidate {candidate.name!r} references unknown operation {candidate.operation}"
            )
        if len(candidate.input_phase_offsets) != len(operation.operands):
            raise MappingProblemError(
                f"candidate {candidate.name!r} has the wrong number of input timing ports"
            )
        covered.add(operation.id)
    missing = set(operations) - covered
    if missing:
        raise MappingProblemError(
            f"operations have no implementation candidates: {sorted(missing)}"
        )


def _candidates_by_operation(
    problem: MappingProblem,
    candidates: tuple[ImplementationCandidate, ...],
) -> dict[int, tuple[ImplementationCandidate, ...]]:
    return {
        operation.id: tuple(item for item in candidates if item.operation == operation.id)
        for operation in problem.operations
    }


def _extract_plan(
    problem: MappingProblem,
    candidates_by_operation: dict[int, tuple[ImplementationCandidate, ...]],
    choose: dict[int, Any],
    output_phase: dict[int, Any],
    use_phase: dict[MappingUse, Any],
    bus_model: _DelayBusModel | None,
    solver: Any,
) -> RealizationPlan:
    realizations: list[SelectedRealization] = []
    selected_by_operation: dict[int, ImplementationCandidate] = {}
    output_values: dict[int, int] = {}

    for operation in problem.operations:
        selected = next(
            item
            for item in candidates_by_operation[operation.id]
            if solver.Value(choose[item.id]) == 1
        )
        phase = int(solver.Value(output_phase[operation.id]))
        selected_by_operation[operation.id] = selected
        output_values[operation.id] = phase
        realizations.append(
            SelectedRealization(
                operation=operation.id,
                candidate=selected.id,
                output_phase=phase,
                entity_cost=selected.entity_cost,
            )
        )

    assigned_bus: dict[int, int] = {}
    if bus_model is not None:
        for (producer, bus), variable in bus_model.assignments.items():
            if solver.BooleanValue(variable):
                assigned_bus[producer] = bus

    operations = {item.id: item for item in problem.operations}
    sources = {item.id: item for item in problem.sources}
    deliveries: list[PlannedDelivery] = []
    transport_taps: dict[int, tuple[int, list[int]]] = {}

    for use in problem.uses():
        phase = int(solver.Value(use_phase[use]))
        if use.producer in operations:
            start = output_values[use.producer]
            if phase == start:
                kind = DeliveryKind.REUSE
                transport_start = None
            else:
                kind = DeliveryKind.PRIVATE_TRANSPORT
                transport_start = start
        else:
            kind, transport_start = source_delivery_kind(sources[use.producer], phase)

        if transport_start is not None and use.producer in assigned_bus:
            kind = DeliveryKind.BUS_TRANSPORT

        deliveries.append(
            PlannedDelivery(
                producer=use.producer,
                consumer=use.consumer,
                operand_index=use.operand_index,
                phase=phase,
                kind=kind,
                transport_start_phase=transport_start,
            )
        )
        if transport_start is not None:
            current = transport_taps.setdefault(use.producer, (transport_start, []))
            current[1].append(phase)

    exact_lifetimes = tuple(
        sorted(
            (
                ExactLifetime(
                    producer=producer,
                    start_phase=start,
                    end_phase=max(taps),
                    tap_phases=tuple(sorted(set(taps))),
                )
                for producer, (start, taps) in transport_taps.items()
            ),
            key=lambda item: (item.start_phase, item.end_phase, item.producer),
        )
    )
    wire_sums = tuple(
        WireSumResource(
            operation=operation.id,
            left_producer=operation.operands[0],
            right_producer=operation.operands[1],
            phase=output_values[operation.id],
        )
        for operation in problem.operations
        if selected_by_operation[operation.id].kind is ImplementationKind.WIRE_SUM
    )

    delay_buses: list[DelayBusResource] = []
    if bus_model is not None:
        for bus, active in enumerate(bus_model.active):
            if not solver.BooleanValue(active):
                continue
            lanes = []
            for producer, assigned in sorted(assigned_bus.items()):
                if assigned != bus:
                    continue
                lifetime = bus_model.lifetimes[producer]
                delivery_phases = tuple(
                    delivery.phase
                    for delivery in deliveries
                    if delivery.producer == producer and delivery.kind is DeliveryKind.BUS_TRANSPORT
                )
                lanes.append(
                    DelayBusLane(
                        producer=producer,
                        start_phase=int(solver.Value(lifetime.start)),
                        end_phase=int(solver.Value(lifetime.end)),
                        delivery_phases=delivery_phases,
                    )
                )
            delay_buses.append(
                DelayBusResource(
                    index=bus,
                    middle_start_phase=int(solver.Value(bus_model.starts[bus])),
                    middle_end_phase=int(solver.Value(bus_model.ends[bus])),
                    lanes=tuple(lanes),
                )
            )

    entity_cost = sum(item.entity_cost for item in realizations)
    bus_producers = set(assigned_bus)
    private_transport_cost = sum(
        item.length for item in exact_lifetimes if item.producer not in bus_producers
    )
    bus_transport_cost = sum(bus.middle_stages + bus.interface_combinators for bus in delay_buses)
    transport_cost = private_transport_cost + bus_transport_cost
    return RealizationPlan(
        realizations=tuple(sorted(realizations, key=lambda item: item.operation)),
        deliveries=tuple(
            sorted(
                deliveries,
                key=lambda item: (
                    item.consumer,
                    -1 if item.operand_index is None else item.operand_index,
                    item.producer,
                ),
            )
        ),
        exact_lifetimes=exact_lifetimes,
        wire_sums=wire_sums,
        entity_cost=entity_cost,
        transport_cost=transport_cost,
        delay_buses=tuple(delay_buses),
    )
