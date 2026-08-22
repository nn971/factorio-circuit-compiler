"""Periodic-state technology mapping with the shared scalar delay-bus resource.

This module deliberately reuses the exact same CP-SAT bus resource model as the stateless joint
mapper. State-cell candidates first turn logical register occurrences into candidate-dependent
physical read windows and transition port phases; the resulting exact lifetimes then compete for
private transport or the already validated shared bus in the same solve.

The implementation remains separate from :mod:`state_solver` for this validation checkpoint. Once
the full Snake recurrence has been exercised with buses, the common scheduling core can be factored
without perturbing the already-green state-cell baseline prematurely.
"""

from __future__ import annotations

from dataclasses import replace
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
    SelectedStateCell,
)
from .problem import MappingProblem, MappingProblemError, MappingUse
from .solver import _DelayBusModel, _LifetimeModel, _add_delay_bus_model
from .state_solver import (
    PeriodicStateMappingOptimizationResult,
    _lifetimes_from_transport,
    _state_register_names,
    _stateful_uses,
    _validate_operation_candidates,
    _validate_state_candidates,
    validate_periodic_state_plan,
)
from .state_templates import StateCellCandidate, ordinary_state_candidates
from .templates import ImplementationCandidate, ordinary_candidates
from .validate import _validate_delay_buses, source_delivery_kind, transport_anchor


def _load_cp_model() -> Any:
    try:
        return import_module("ortools.sat.python.cp_model")
    except ModuleNotFoundError as exc:  # pragma: no cover - optional environment
        raise RuntimeError(
            "periodic state technology mapping requires OR-Tools; run with "
            "`uv run --with 'ortools>=9.14,<10' ...`"
        ) from exc


def solve_periodic_state_bus_mapping_problem(
    problem: MappingProblem,
    *,
    candidates: tuple[ImplementationCandidate, ...] | None = None,
    state_candidates: tuple[StateCellCandidate, ...] | None = None,
    max_delay_buses: int = 1,
    delay_bus_capacity: int = 256,
    time_limit_seconds: float = 30.0,
    workers: int = 1,
) -> PeriodicStateMappingOptimizationResult:
    """Jointly solve periodic state timing and shared scalar exact transport.

    The operation/state-cell candidate contracts are identical to the private-only state solver.
    ``max_delay_buses=0`` disables the shared resource and provides a useful parity baseline.
    """

    if problem.period is None:
        raise MappingProblemError("periodic state bus solver requires a prescribed mapping period")
    if not problem.state_reads or not problem.state_transitions:
        raise MappingProblemError("periodic state bus solver requires state reads and transitions")
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
    selected_state_candidates = (
        state_candidates if state_candidates is not None else ordinary_state_candidates(problem)
    )
    _validate_operation_candidates(problem, selected_candidates)
    _validate_state_candidates(problem, selected_state_candidates)

    cp_model = _load_cp_model()
    model = cp_model.CpModel()
    period = problem.period
    operations = {item.id: item for item in problem.operations}
    sources = {item.id: item for item in problem.sources}
    state_reads = {item.id: item for item in problem.state_reads}
    sinks = {item.id: item for item in problem.sinks}
    transitions = {item.id: item for item in problem.state_transitions}
    register_names = _state_register_names(problem)

    candidates_by_operation = {
        operation.id: tuple(item for item in selected_candidates if item.operation == operation.id)
        for operation in problem.operations
    }
    state_candidates_by_register = {
        register_name: tuple(
            item
            for item in selected_state_candidates
            if item.register_name == register_name
        )
        for register_name in register_names
    }

    choose: dict[int, Any] = {}
    output_phase: dict[int, Any] = {}
    for operation in problem.operations:
        output_phase[operation.id] = model.NewIntVar(
            0,
            problem.horizon,
            f"state_bus_output_phase_{operation.id}",
        )
        choices = []
        for candidate in candidates_by_operation[operation.id]:
            variable = model.NewBoolVar(f"state_bus_candidate_{candidate.id}")
            choose[candidate.id] = variable
            choices.append(variable)
        model.Add(sum(choices) == 1)

    state_choose: dict[int, Any] = {}
    state_base_phase: dict[str, Any] = {}
    for register_name in register_names:
        state_base_phase[register_name] = model.NewIntVar(
            0,
            period - 1,
            f"state_bus_base_phase_{register_name}",
        )
        choices = []
        for candidate in state_candidates_by_register[register_name]:
            variable = model.NewBoolVar(f"state_bus_cell_candidate_{candidate.id}")
            state_choose[candidate.id] = variable
            choices.append(variable)
        model.Add(sum(choices) == 1)

    uses = _stateful_uses(problem)
    use_phase: dict[MappingUse, Any] = {}
    for use in uses:
        if use.operand_index is None:
            use_phase[use] = model.NewConstant(sinks[use.consumer].phase)
        else:
            use_phase[use] = model.NewIntVar(
                0,
                problem.horizon,
                f"state_bus_use_{use.producer}_{use.consumer}_{use.operand_index}",
            )

    for operation in problem.operations:
        for candidate in candidates_by_operation[operation.id]:
            for operand_index, offset in enumerate(candidate.input_phase_offsets):
                use = MappingUse(
                    operation.operands[operand_index],
                    operation.id,
                    operand_index,
                )
                model.Add(
                    use_phase[use] == output_phase[operation.id] + offset
                ).OnlyEnforceIf(choose[candidate.id])

    for register_name, register_candidates in state_candidates_by_register.items():
        base = state_base_phase[register_name]
        for candidate in register_candidates:
            selected = state_choose[candidate.id]
            for port in candidate.transition_ports:
                transition = transitions[port.transition]
                next_read = base + (transition.logical_offset + 1) * period
                if transition.value is not None:
                    if port.value_phase_offset is None:
                        raise MappingProblemError(
                            "selected state-cell candidate has no data timing for a data transition"
                        )
                    use = MappingUse(transition.value, transition.id, 0)
                    model.Add(
                        use_phase[use] == next_read + port.value_phase_offset
                    ).OnlyEnforceIf(selected)
                if transition.when is not None:
                    if port.when_phase_offset is None:
                        raise MappingProblemError(
                            "selected state-cell candidate has no condition timing for a "
                            "condition transition"
                        )
                    use = MappingUse(transition.when, transition.id, 1)
                    model.Add(
                        use_phase[use] == next_read + port.when_phase_offset
                    ).OnlyEnforceIf(selected)

    outgoing: dict[int, list[MappingUse]] = {
        value_id: [] for value_id in problem.value_ids
    }
    state_read_start: dict[int, Any] = {}
    state_read_last_free: dict[int, Any] = {}
    for read in problem.state_reads:
        if read.logical_offset < 0:
            raise MappingProblemError(
                "first periodic state bus solver requires non-negative state-read offsets"
            )
        start = state_base_phase[read.register_name] + read.logical_offset * period
        state_read_start[read.id] = start
        state_read_last_free[read.id] = start + period - 1

    for use in uses:
        phase = use_phase[use]
        outgoing[use.producer].append(use)
        if use.producer in operations:
            model.Add(phase >= output_phase[use.producer])
        elif use.producer in sources:
            model.Add(phase >= sources[use.producer].start_phase)
        elif use.producer in state_reads:
            model.Add(phase >= state_read_start[use.producer])
        else:  # pragma: no cover - MappingProblem validates the namespace
            raise AssertionError(use.producer)

    lifetimes: dict[int, _LifetimeModel] = {}
    for operation in problem.operations:
        operation_uses = tuple(outgoing[operation.id])
        end = model.NewIntVar(
            0,
            problem.horizon,
            f"state_bus_end_{operation.id}",
        )
        model.AddMaxEquality(
            end,
            [output_phase[operation.id], *(use_phase[use] for use in operation_uses)],
        )
        length = model.NewIntVar(
            0,
            problem.horizon,
            f"state_bus_length_{operation.id}",
        )
        model.Add(length == end - output_phase[operation.id])
        lifetimes[operation.id] = _LifetimeModel(
            producer=operation.id,
            shape=operation.shape,
            start=output_phase[operation.id],
            end=end,
            length=length,
            uses=operation_uses,
        )

    for source in problem.sources:
        anchor = transport_anchor(source)
        source_uses = tuple(outgoing[source.id])
        if anchor is None or not source_uses:
            continue
        end = model.NewIntVar(
            anchor,
            problem.horizon,
            f"state_bus_source_end_{source.id}",
        )
        model.AddMaxEquality(
            end,
            [model.NewConstant(anchor), *(use_phase[use] for use in source_uses)],
        )
        length = model.NewIntVar(
            0,
            problem.horizon,
            f"state_bus_source_length_{source.id}",
        )
        model.Add(length == end - anchor)
        lifetimes[source.id] = _LifetimeModel(
            producer=source.id,
            shape=source.shape,
            start=model.NewConstant(anchor),
            end=end,
            length=length,
            uses=source_uses,
        )

    for read in problem.state_reads:
        read_uses = tuple(outgoing[read.id])
        if not read_uses:
            continue
        last_use = model.NewIntVar(
            0,
            problem.horizon,
            f"state_bus_read_last_use_{read.id}",
        )
        model.AddMaxEquality(last_use, [use_phase[use] for use in read_uses])
        length = model.NewIntVar(
            0,
            problem.horizon,
            f"state_bus_read_length_{read.id}",
        )
        model.AddMaxEquality(
            length,
            [0, last_use - state_read_last_free[read.id]],
        )
        # Register reads are vectors in the current semantic IR. ``end`` may lie beyond the finite
        # mapping horizon when the selected stable window outlives every represented use; this is
        # harmless because vector lifetimes are never bus lanes in the current scalar bus model.
        lifetimes[read.id] = _LifetimeModel(
            producer=read.id,
            shape=PayloadShape.VECTOR,
            start=state_read_last_free[read.id],
            end=state_read_last_free[read.id] + length,
            length=length,
            uses=read_uses,
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
    entity_terms.extend(
        candidate.entity_cost * state_choose[candidate.id]
        for candidate in selected_state_candidates
    )
    model.Minimize(sum(entity_terms) + sum(transport_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_seconds)
    solver.parameters.num_search_workers = workers
    status_code = solver.Solve(model)
    status = str(solver.StatusName(status_code))
    if status.upper() not in {"OPTIMAL", "FEASIBLE"}:
        raise MappingProblemError(f"periodic state bus mapping failed with status {status}")

    plan = _extract_plan(
        problem,
        selected_candidates,
        selected_state_candidates,
        choose,
        state_choose,
        output_phase,
        state_base_phase,
        state_read_start,
        state_read_last_free,
        use_phase,
        uses,
        bus_model,
        solver,
    )
    _validate_bus_state_plan(
        problem,
        selected_candidates,
        selected_state_candidates,
        plan,
    )
    return PeriodicStateMappingOptimizationResult(
        status=status,
        plan=plan,
        wall_time_seconds=float(solver.WallTime()),
    )


def _extract_plan(
    problem: MappingProblem,
    candidates: tuple[ImplementationCandidate, ...],
    state_candidates: tuple[StateCellCandidate, ...],
    choose: dict[int, Any],
    state_choose: dict[int, Any],
    output_phase: dict[int, Any],
    state_base_phase: dict[str, Any],
    state_read_start: dict[int, Any],
    state_read_last_free: dict[int, Any],
    use_phase: dict[MappingUse, Any],
    uses: tuple[MappingUse, ...],
    bus_model: _DelayBusModel | None,
    solver: Any,
) -> RealizationPlan:
    candidates_by_operation = {
        operation.id: tuple(item for item in candidates if item.operation == operation.id)
        for operation in problem.operations
    }
    state_candidates_by_register = {
        name: tuple(item for item in state_candidates if item.register_name == name)
        for name in _state_register_names(problem)
    }

    realizations: list[SelectedRealization] = []
    output_values: dict[int, int] = {}
    for operation in problem.operations:
        selected = next(
            item
            for item in candidates_by_operation[operation.id]
            if solver.BooleanValue(choose[item.id])
        )
        phase = int(solver.Value(output_phase[operation.id]))
        output_values[operation.id] = phase
        realizations.append(
            SelectedRealization(
                operation=operation.id,
                candidate=selected.id,
                output_phase=phase,
                entity_cost=selected.entity_cost,
            )
        )

    state_cells: list[SelectedStateCell] = []
    for register_name in _state_register_names(problem):
        selected = next(
            item
            for item in state_candidates_by_register[register_name]
            if solver.BooleanValue(state_choose[item.id])
        )
        state_cells.append(
            SelectedStateCell(
                register_name=register_name,
                candidate=selected.id,
                base_read_phase=int(solver.Value(state_base_phase[register_name])),
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
    reads = {item.id: item for item in problem.state_reads}
    deliveries: list[PlannedDelivery] = []
    transport_taps: dict[int, tuple[int, list[int]]] = {}

    for use in uses:
        phase = int(solver.Value(use_phase[use]))
        if use.producer in operations:
            start = output_values[use.producer]
            if phase == start:
                kind = DeliveryKind.REUSE
                transport_start = None
            else:
                kind = DeliveryKind.PRIVATE_TRANSPORT
                transport_start = start
        elif use.producer in sources:
            kind, transport_start = source_delivery_kind(sources[use.producer], phase)
        elif use.producer in reads:
            start = int(solver.Value(state_read_start[use.producer]))
            last_free = int(solver.Value(state_read_last_free[use.producer]))
            if phase < start:  # pragma: no cover - solver constraint
                raise AssertionError(
                    "solver consumed a state read before its candidate read phase"
                )
            if phase <= last_free:
                kind = DeliveryKind.REUSE
                transport_start = None
            else:
                kind = DeliveryKind.PRIVATE_TRANSPORT
                transport_start = last_free
        else:  # pragma: no cover - MappingProblem validates the namespace
            raise AssertionError(use.producer)

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
            if current[0] != transport_start:  # pragma: no cover - one availability contract
                raise AssertionError("one producer acquired inconsistent transport anchors")
            current[1].append(phase)

    exact_lifetimes = _lifetimes_from_transport(transport_taps)

    delay_buses: list[DelayBusResource] = []
    if bus_model is not None:
        for bus, active in enumerate(bus_model.active):
            if not solver.BooleanValue(active):
                continue
            lanes: list[DelayBusLane] = []
            for producer, assigned in sorted(assigned_bus.items()):
                if assigned != bus:
                    continue
                lifetime = bus_model.lifetimes[producer]
                delivery_phases = tuple(
                    delivery.phase
                    for delivery in deliveries
                    if delivery.producer == producer
                    and delivery.kind is DeliveryKind.BUS_TRANSPORT
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

    entity_cost = sum(item.entity_cost for item in realizations) + sum(
        item.entity_cost for item in state_cells
    )
    bus_producers = set(assigned_bus)
    private_transport_cost = sum(
        item.length for item in exact_lifetimes if item.producer not in bus_producers
    )
    bus_transport_cost = sum(
        bus.middle_stages + bus.interface_combinators for bus in delay_buses
    )
    transport_cost = private_transport_cost + bus_transport_cost

    return RealizationPlan(
        realizations=tuple(sorted(realizations, key=lambda item: item.operation)),
        state_cells=tuple(sorted(state_cells, key=lambda item: item.register_name)),
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
        wire_sums=(),
        delay_buses=tuple(delay_buses),
        entity_cost=entity_cost,
        transport_cost=transport_cost,
    )


def _validate_bus_state_plan(
    problem: MappingProblem,
    candidates: tuple[ImplementationCandidate, ...],
    state_candidates: tuple[StateCellCandidate, ...],
    plan: RealizationPlan,
) -> None:
    """Validate state timing through the baseline validator, then validate bus realization."""

    private_shadow = replace(
        plan,
        deliveries=tuple(
            replace(delivery, kind=DeliveryKind.PRIVATE_TRANSPORT)
            if delivery.kind is DeliveryKind.BUS_TRANSPORT
            else delivery
            for delivery in plan.deliveries
        ),
        delay_buses=(),
        transport_cost=sum(item.length for item in plan.exact_lifetimes),
    )
    validate_periodic_state_plan(
        problem,
        candidates,
        state_candidates,
        private_shadow,
    )

    bus_producers = _validate_delay_buses(plan, plan.exact_lifetimes)
    private_transport_cost = sum(
        item.length for item in plan.exact_lifetimes if item.producer not in bus_producers
    )
    bus_transport_cost = sum(
        bus.middle_stages + bus.interface_combinators for bus in plan.delay_buses
    )
    if plan.transport_cost != private_transport_cost + bus_transport_cost:
        raise MappingProblemError("periodic state bus transport cost is inconsistent")
