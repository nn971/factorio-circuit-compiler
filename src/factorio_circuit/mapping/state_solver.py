"""First joint periodic-state technology-mapping solver.

This milestone makes ordinary Freeze recurrence timing a candidate-owned target choice. It jointly
chooses ordinary computation phases, each selected Freeze cell's phase within the prescribed period,
transition input phases implied by that candidate, free state-read reuse, and prefix-shared residual
exact transport. Delay buses and non-ordinary computation candidates remain outside this first
stateful checkpoint so state-cell timing can be validated in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any

from .plan import (
    DeliveryKind,
    ExactLifetime,
    PlannedDelivery,
    RealizationPlan,
    SelectedRealization,
    SelectedStateCell,
)
from .problem import MappingProblem, MappingProblemError, MappingUse
from .state_templates import StateCellCandidate, ordinary_freeze_state_candidates
from .templates import (
    CandidateOutputMode,
    ImplementationCandidate,
    ImplementationKind,
    ordinary_candidates,
)
from .validate import source_delivery_kind, transport_anchor


@dataclass(frozen=True, slots=True)
class PeriodicStateMappingOptimizationResult:
    status: str
    plan: RealizationPlan
    wall_time_seconds: float

    @property
    def proven_optimal(self) -> bool:
        return self.status.upper() == "OPTIMAL"


def _load_cp_model() -> Any:
    try:
        return import_module("ortools.sat.python.cp_model")
    except ModuleNotFoundError as exc:  # pragma: no cover - optional environment
        raise RuntimeError(
            "periodic state technology mapping requires OR-Tools; run with "
            "`uv run --with 'ortools>=9.14,<10' ...`"
        ) from exc


def _stateful_uses(problem: MappingProblem) -> tuple[MappingUse, ...]:
    result = [
        MappingUse(producer, operation.id, operand_index)
        for operation in problem.operations
        for operand_index, producer in enumerate(operation.operands)
    ]
    result.extend(MappingUse(sink.value, sink.id, None) for sink in problem.sinks)
    for transition in problem.state_transitions:
        if transition.value is not None:
            result.append(MappingUse(transition.value, transition.id, 0))
        if transition.when is not None:
            result.append(MappingUse(transition.when, transition.id, 1))
    return tuple(result)


def _state_register_names(problem: MappingProblem) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            [item.register_name for item in problem.state_reads]
            + [item.register_name for item in problem.state_transitions]
        )
    )


def solve_periodic_state_mapping_problem(
    problem: MappingProblem,
    *,
    candidates: tuple[ImplementationCandidate, ...] | None = None,
    state_candidates: tuple[StateCellCandidate, ...] | None = None,
    time_limit_seconds: float = 30.0,
    workers: int = 1,
) -> PeriodicStateMappingOptimizationResult:
    """Jointly solve a periodic recurrence using the first ordinary Freeze state-cell family."""

    if problem.period is None:
        raise MappingProblemError("periodic state solver requires a prescribed mapping period")
    if not problem.state_reads or not problem.state_transitions:
        raise MappingProblemError("periodic state solver requires state reads and transitions")
    if time_limit_seconds <= 0:
        raise ValueError("time_limit_seconds must be positive")
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")

    selected_candidates = candidates if candidates is not None else ordinary_candidates(problem)
    selected_state_candidates = (
        state_candidates
        if state_candidates is not None
        else ordinary_freeze_state_candidates(problem)
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
    candidates_by_operation = {
        operation.id: tuple(
            item for item in selected_candidates if item.operation == operation.id
        )
        for operation in problem.operations
    }
    register_names = _state_register_names(problem)
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
            f"state_mapping_output_phase_{operation.id}",
        )
        operation_choices = []
        for candidate in candidates_by_operation[operation.id]:
            variable = model.NewBoolVar(f"state_mapping_candidate_{candidate.id}")
            choose[candidate.id] = variable
            operation_choices.append(variable)
        model.Add(sum(operation_choices) == 1)

    state_choose: dict[int, Any] = {}
    state_base_phase: dict[str, Any] = {}
    for register_name in register_names:
        state_base_phase[register_name] = model.NewIntVar(
            0,
            period - 1,
            f"state_mapping_base_phase_{register_name}",
        )
        choices = []
        for candidate in state_candidates_by_register[register_name]:
            variable = model.NewBoolVar(f"state_mapping_cell_candidate_{candidate.id}")
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
                f"state_mapping_use_{use.producer}_{use.consumer}_{use.operand_index}",
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
                "first periodic state solver requires non-negative state-read offsets"
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
        else:  # pragma: no cover - MappingProblem validates the value namespace
            raise AssertionError(use.producer)

    transport_terms: list[Any] = []
    for operation in problem.operations:
        operation_uses = tuple(outgoing[operation.id])
        end = model.NewIntVar(
            0,
            problem.horizon,
            f"state_mapping_end_{operation.id}",
        )
        model.AddMaxEquality(
            end,
            [output_phase[operation.id], *(use_phase[use] for use in operation_uses)],
        )
        length = model.NewIntVar(
            0,
            problem.horizon,
            f"state_mapping_length_{operation.id}",
        )
        model.Add(length == end - output_phase[operation.id])
        transport_terms.append(length)

    for source in problem.sources:
        anchor = transport_anchor(source)
        source_uses = tuple(outgoing[source.id])
        if anchor is None or not source_uses:
            continue
        end = model.NewIntVar(
            anchor,
            problem.horizon,
            f"state_mapping_source_end_{source.id}",
        )
        model.AddMaxEquality(
            end,
            [model.NewConstant(anchor), *(use_phase[use] for use in source_uses)],
        )
        length = model.NewIntVar(
            0,
            problem.horizon,
            f"state_mapping_source_length_{source.id}",
        )
        model.Add(length == end - anchor)
        transport_terms.append(length)

    for read in problem.state_reads:
        read_uses = tuple(outgoing[read.id])
        if not read_uses:
            continue
        last_use = model.NewIntVar(
            0,
            problem.horizon,
            f"state_mapping_read_last_use_{read.id}",
        )
        model.AddMaxEquality(last_use, [use_phase[use] for use in read_uses])
        length = model.NewIntVar(
            0,
            problem.horizon,
            f"state_mapping_read_length_{read.id}",
        )
        model.AddMaxEquality(
            length,
            [0, last_use - state_read_last_free[read.id]],
        )
        transport_terms.append(length)

    entity_terms = [
        candidate.entity_cost * choose[candidate.id]
        for candidate in selected_candidates
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
        raise MappingProblemError(f"periodic state mapping failed with status {status}")

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
        solver,
    )
    validate_periodic_state_plan(
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


def _validate_operation_candidates(
    problem: MappingProblem,
    candidates: tuple[ImplementationCandidate, ...],
) -> None:
    candidate_ids = [item.id for item in candidates]
    if len(set(candidate_ids)) != len(candidate_ids):
        raise MappingProblemError("periodic operation candidate ids must be unique")
    operations = {item.id: item for item in problem.operations}
    covered: set[int] = set()
    for candidate in candidates:
        operation = operations.get(candidate.operation)
        if operation is None:
            raise MappingProblemError(
                "periodic operation candidate references an unknown operation"
            )
        if candidate.output_mode is not CandidateOutputMode.EXACT:
            raise MappingProblemError(
                "first periodic state solver requires exact operation outputs"
            )
        if candidate.kind is ImplementationKind.WIRE_SUM:
            raise MappingProblemError(
                "first periodic state solver does not yet admit wire-sum candidates"
            )
        if len(candidate.input_phase_offsets) != len(operation.operands):
            raise MappingProblemError(
                "periodic operation candidate has the wrong input port count"
            )
        covered.add(operation.id)
    missing = set(operations) - covered
    if missing:
        raise MappingProblemError(
            f"periodic operations have no implementation candidates: {sorted(missing)}"
        )


def _validate_state_candidates(
    problem: MappingProblem,
    candidates: tuple[StateCellCandidate, ...],
) -> None:
    candidate_ids = [item.id for item in candidates]
    if len(set(candidate_ids)) != len(candidate_ids):
        raise MappingProblemError("state-cell candidate ids must be unique")
    expected_registers = set(_state_register_names(problem))
    covered_registers = {item.register_name for item in candidates}
    missing = expected_registers - covered_registers
    extra = covered_registers - expected_registers
    if missing:
        raise MappingProblemError(
            "periodic registers have no supported state-cell implementation candidates: "
            f"{sorted(missing)}"
        )
    if extra:
        raise MappingProblemError(
            f"state-cell candidates reference unknown periodic registers: {sorted(extra)}"
        )
    transition_ids = {item.id for item in problem.state_transitions}
    for candidate in candidates:
        ports = {item.transition for item in candidate.transition_ports}
        expected_ports = {
            item.id
            for item in problem.state_transitions
            if item.register_name == candidate.register_name
        }
        if ports != expected_ports:
            raise MappingProblemError(
                f"state-cell candidate {candidate.name!r} does not cover exactly its "
                "register transitions"
            )
        if not ports <= transition_ids:  # pragma: no cover - implied by equality above
            raise MappingProblemError("state-cell candidate references an unknown transition")


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
    entity_cost = sum(item.entity_cost for item in realizations) + sum(
        item.entity_cost for item in state_cells
    )
    transport_cost = sum(item.length for item in exact_lifetimes)
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
        delay_buses=(),
        entity_cost=entity_cost,
        transport_cost=transport_cost,
    )


def validate_periodic_state_plan(
    problem: MappingProblem,
    candidates: tuple[ImplementationCandidate, ...],
    state_candidates: tuple[StateCellCandidate, ...],
    plan: RealizationPlan,
) -> None:
    """Independently validate the first periodic state realization plan."""

    if problem.period is None:
        raise MappingProblemError("stateful realization plan has no prescribed period")
    period = problem.period
    uses = _stateful_uses(problem)
    deliveries = {
        MappingUse(item.producer, item.consumer, item.operand_index): item
        for item in plan.deliveries
    }
    if len(deliveries) != len(plan.deliveries) or set(deliveries) != set(uses):
        raise MappingProblemError(
            "periodic state plan must satisfy every semantic use exactly once"
        )

    candidate_by_id = {item.id: item for item in candidates}
    realization_by_operation = {item.operation: item for item in plan.realizations}
    if set(realization_by_operation) != {item.id for item in problem.operations}:
        raise MappingProblemError(
            "periodic state plan must realize every operation exactly once"
        )
    entity_cost = 0
    for operation in problem.operations:
        realization = realization_by_operation[operation.id]
        candidate = candidate_by_id.get(realization.candidate)
        if candidate is None or candidate.operation != operation.id:
            raise MappingProblemError(
                "periodic realization selects the wrong operation candidate"
            )
        if realization.entity_cost != candidate.entity_cost:
            raise MappingProblemError(
                "periodic operation realization cost disagrees with candidate"
            )
        entity_cost += realization.entity_cost
        for operand_index, offset in enumerate(candidate.input_phase_offsets):
            use = MappingUse(
                operation.operands[operand_index],
                operation.id,
                operand_index,
            )
            if deliveries[use].phase != realization.output_phase + offset:
                raise MappingProblemError(
                    "periodic operation candidate timing equation is violated"
                )

    state_candidate_by_id = {item.id: item for item in state_candidates}
    state_cell_by_register = {item.register_name: item for item in plan.state_cells}
    expected_registers = set(_state_register_names(problem))
    if set(state_cell_by_register) != expected_registers:
        raise MappingProblemError(
            "periodic state plan must select exactly one cell per register"
        )
    transitions = {item.id: item for item in problem.state_transitions}
    for register_name, cell in state_cell_by_register.items():
        candidate = state_candidate_by_id.get(cell.candidate)
        if candidate is None or candidate.register_name != register_name:
            raise MappingProblemError(
                "periodic state plan selects the wrong state-cell candidate"
            )
        if cell.entity_cost != candidate.entity_cost:
            raise MappingProblemError("state-cell realization cost disagrees with candidate")
        if not 0 <= cell.base_read_phase < period:
            raise MappingProblemError(
                "state-cell base read phase lies outside one logical period"
            )
        entity_cost += cell.entity_cost
        for port in candidate.transition_ports:
            transition = transitions[port.transition]
            next_read = cell.base_read_phase + (transition.logical_offset + 1) * period
            if transition.value is not None:
                if port.value_phase_offset is None:
                    raise MappingProblemError(
                        "state-cell candidate is missing data port timing"
                    )
                use = MappingUse(transition.value, transition.id, 0)
                if deliveries[use].phase != next_read + port.value_phase_offset:
                    raise MappingProblemError(
                        "state-cell data port timing equation is violated"
                    )
            if transition.when is not None:
                if port.when_phase_offset is None:
                    raise MappingProblemError(
                        "state-cell candidate is missing condition port timing"
                    )
                use = MappingUse(transition.when, transition.id, 1)
                if deliveries[use].phase != next_read + port.when_phase_offset:
                    raise MappingProblemError(
                        "state-cell condition port timing equation is violated"
                    )

    for sink in problem.sinks:
        delivery = deliveries[MappingUse(sink.value, sink.id, None)]
        if delivery.phase != sink.phase:
            raise MappingProblemError(
                "periodic sink delivery phase violates its fixed contract"
            )

    operations = {item.id: item for item in problem.operations}
    sources = {item.id: item for item in problem.sources}
    reads = {item.id: item for item in problem.state_reads}
    expected_transport: dict[int, tuple[int, list[int]]] = {}
    for use, delivery in deliveries.items():
        if not 0 <= delivery.phase <= problem.horizon:
            raise MappingProblemError(
                "periodic delivery lies outside the mapping horizon"
            )
        if use.producer in operations:
            start = realization_by_operation[use.producer].output_phase
            if delivery.phase < start:
                raise MappingProblemError(
                    "periodic operation result is consumed before production"
                )
            transport_start = start if delivery.phase > start else None
            free_kind = DeliveryKind.REUSE
        elif use.producer in sources:
            source = sources[use.producer]
            if delivery.phase < source.start_phase:
                raise MappingProblemError(
                    "periodic source is consumed before availability"
                )
            free_kind, transport_start = source_delivery_kind(source, delivery.phase)
        elif use.producer in reads:
            read = reads[use.producer]
            cell = state_cell_by_register[read.register_name]
            start = cell.base_read_phase + read.logical_offset * period
            last_free = start + period - 1
            if delivery.phase < start:
                raise MappingProblemError(
                    "state value is consumed before the selected read phase"
                )
            if delivery.phase <= last_free:
                free_kind = DeliveryKind.REUSE
                transport_start = None
            else:
                free_kind = DeliveryKind.PRIVATE_TRANSPORT
                transport_start = last_free
        else:  # pragma: no cover - MappingProblem validates the namespace
            raise AssertionError(use.producer)

        if transport_start is None:
            if delivery.kind is not free_kind or delivery.transport_start_phase is not None:
                raise MappingProblemError(
                    "periodic free delivery disagrees with availability"
                )
            continue
        if delivery.kind is not DeliveryKind.PRIVATE_TRANSPORT:
            raise MappingProblemError(
                "first periodic state solver requires private exact transport"
            )
        if delivery.transport_start_phase != transport_start:
            raise MappingProblemError(
                "periodic transport start disagrees with availability"
            )
        current = expected_transport.setdefault(use.producer, (transport_start, []))
        if current[0] != transport_start:
            raise MappingProblemError(
                "periodic producer acquired inconsistent transport anchors"
            )
        current[1].append(delivery.phase)

    expected_lifetimes = _lifetimes_from_transport(expected_transport)
    actual_lifetimes = tuple(
        sorted(
            plan.exact_lifetimes,
            key=lambda item: (item.start_phase, item.end_phase, item.producer),
        )
    )
    if actual_lifetimes != expected_lifetimes:
        raise MappingProblemError(
            "periodic exact lifetimes disagree with planned deliveries"
        )
    if plan.wire_sums or plan.delay_buses:
        raise MappingProblemError(
            "first periodic state plan cannot contain shared resources"
        )
    if plan.entity_cost != entity_cost:
        raise MappingProblemError("periodic state plan entity cost is inconsistent")
    if plan.transport_cost != sum(item.length for item in expected_lifetimes):
        raise MappingProblemError("periodic state plan transport cost is inconsistent")


def _lifetimes_from_transport(
    transport: dict[int, tuple[int, list[int]]],
) -> tuple[ExactLifetime, ...]:
    return tuple(
        sorted(
            (
                ExactLifetime(
                    producer=producer,
                    start_phase=start,
                    end_phase=max(taps),
                    tap_phases=tuple(sorted(set(taps))),
                )
                for producer, (start, taps) in transport.items()
            ),
            key=lambda item: (item.start_phase, item.end_phase, item.producer),
        )
    )
