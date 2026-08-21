"""Joint candidate selection and physical-phase optimization for the first mapping milestone.

The solver owns target timing: semantic operations do not arrive with precomputed physical latency
windows.  Each selected implementation candidate contributes its own input/output phase equations.
The first milestone supports finite local candidates, free source reuse/observation, and prefix-shared
private exact transport.  Shared buses and other resource families are intentionally deferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any

from .plan import DeliveryKind, ExactLifetime, PlannedDelivery, RealizationPlan, SelectedRealization
from .problem import MappingProblem, MappingProblemError, MappingSource, MappingSourceMode, MappingUse
from .templates import CandidateOutputMode, ImplementationCandidate, ordinary_candidates


@dataclass(frozen=True, slots=True)
class MappingOptimizationResult:
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
            "temporal technology mapping requires OR-Tools for this invocation; run with "
            "`uv run --with 'ortools>=9.14,<10' ...`"
        ) from exc


def solve_mapping_problem(
    problem: MappingProblem,
    *,
    candidates: tuple[ImplementationCandidate, ...] | None = None,
    time_limit_seconds: float = 30.0,
    workers: int = 1,
) -> MappingOptimizationResult:
    """Choose implementations and physical phases in one CP-SAT model.

    The objective is abstract implementation entities plus private exact-transport stages.  Source
    ``STABLE``/``OBSERVABLE`` windows are free through their last valid phase; later uses begin one
    exact lifetime at that boundary.  Operation outputs are conservatively EXACT in this milestone.
    """

    if time_limit_seconds <= 0:
        raise ValueError("time_limit_seconds must be positive")
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")

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
                model.Add(
                    use_phase[use] == output_phase[operation.id] + offset
                ).OnlyEnforceIf(choose[candidate.id])

    outgoing: dict[int, list[Any]] = {item: [] for item in problem.value_ids}
    for use in problem.uses():
        phase = use_phase[use]
        outgoing[use.producer].append(phase)
        if use.producer in operations:
            model.Add(phase >= output_phase[use.producer])
        else:
            model.Add(phase >= sources[use.producer].start_phase)

    lifetime_lengths: list[Any] = []
    for operation in problem.operations:
        uses = outgoing[operation.id]
        end = model.NewIntVar(0, problem.horizon, f"mapping_exact_end_{operation.id}")
        model.AddMaxEquality(end, [output_phase[operation.id], *uses])
        length = model.NewIntVar(0, problem.horizon, f"mapping_exact_length_{operation.id}")
        model.Add(length == end - output_phase[operation.id])
        lifetime_lengths.append(length)

    for source in problem.sources:
        anchor = _transport_anchor(source)
        if anchor is None:
            continue
        uses = outgoing[source.id]
        if not uses:
            continue
        end = model.NewIntVar(anchor, problem.horizon, f"mapping_source_end_{source.id}")
        model.AddMaxEquality(end, [model.NewConstant(anchor), *uses])
        length = model.NewIntVar(0, problem.horizon, f"mapping_source_length_{source.id}")
        model.Add(length == end - anchor)
        lifetime_lengths.append(length)

    entity_terms = [
        candidate.entity_cost * choose[candidate.id] for candidate in selected_candidates
    ]
    model.Minimize(sum(entity_terms) + sum(lifetime_lengths))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_seconds)
    solver.parameters.num_search_workers = workers
    status = solver.Solve(model)
    status_name = solver.StatusName(status)
    if status_name not in {"OPTIMAL", "FEASIBLE"}:
        raise MappingProblemError(f"temporal technology mapping failed with status {status_name}")

    plan = _extract_plan(
        problem,
        selected_candidates,
        candidates_by_operation,
        choose,
        output_phase,
        use_phase,
        solver,
    )
    validate_realization_plan(problem, selected_candidates, plan)
    return MappingOptimizationResult(
        status=status_name,
        plan=plan,
        wall_time_seconds=float(solver.WallTime()),
    )


def validate_realization_plan(
    problem: MappingProblem,
    candidates: tuple[ImplementationCandidate, ...],
    plan: RealizationPlan,
) -> None:
    """Independently validate a selected first-milestone realization plan."""

    candidate_by_id = {item.id: item for item in candidates}
    if len(candidate_by_id) != len(candidates):
        raise MappingProblemError("mapping candidates must have unique ids")

    realization_by_operation = {item.operation: item for item in plan.realizations}
    if len(realization_by_operation) != len(plan.realizations):
        raise MappingProblemError("realization plan contains duplicate operation realizations")
    if set(realization_by_operation) != {item.id for item in problem.operations}:
        raise MappingProblemError("realization plan must realize every semantic operation exactly once")

    deliveries = {
        MappingUse(item.producer, item.consumer, item.operand_index): item
        for item in plan.deliveries
    }
    if len(deliveries) != len(plan.deliveries):
        raise MappingProblemError("realization plan contains duplicate semantic deliveries")
    expected_uses = set(problem.uses())
    if set(deliveries) != expected_uses:
        raise MappingProblemError("realization plan must satisfy every semantic use exactly once")

    operations = {item.id: item for item in problem.operations}
    sources = {item.id: item for item in problem.sources}
    sinks = {item.id: item for item in problem.sinks}

    entity_cost = 0
    for operation_id, realization in realization_by_operation.items():
        candidate = candidate_by_id.get(realization.candidate)
        if candidate is None or candidate.operation != operation_id:
            raise MappingProblemError("realization selects a candidate for the wrong operation")
        if candidate.output_mode is not CandidateOutputMode.EXACT:
            raise MappingProblemError("first-milestone validator requires EXACT candidate outputs")
        if realization.entity_cost != candidate.entity_cost:
            raise MappingProblemError("realization entity cost disagrees with its candidate")
        if not 0 <= realization.output_phase <= problem.horizon:
            raise MappingProblemError("realization output phase lies outside the mapping horizon")
        entity_cost += realization.entity_cost

        operation = operations[operation_id]
        for operand_index, offset in enumerate(candidate.input_phase_offsets):
            use = MappingUse(operation.operands[operand_index], operation_id, operand_index)
            expected_phase = realization.output_phase + offset
            if expected_phase < 0 or deliveries[use].phase != expected_phase:
                raise MappingProblemError("candidate timing equation disagrees with planned delivery")

    for sink in problem.sinks:
        delivery = deliveries[MappingUse(sink.value, sink.id, None)]
        if delivery.phase != sink.phase:
            raise MappingProblemError("sink delivery phase disagrees with the fixed sink contract")

    expected_transport: dict[int, tuple[int, list[int]]] = {}
    for use, delivery in deliveries.items():
        if not 0 <= delivery.phase <= problem.horizon:
            raise MappingProblemError("planned delivery phase lies outside the mapping horizon")
        if use.producer in operations:
            start = realization_by_operation[use.producer].output_phase
            if delivery.phase < start:
                raise MappingProblemError("operation result is consumed before it is produced")
            expected_kind = (
                DeliveryKind.REUSE
                if delivery.phase == start
                else DeliveryKind.PRIVATE_TRANSPORT
            )
            transport_start = start if expected_kind is DeliveryKind.PRIVATE_TRANSPORT else None
        else:
            source = sources[use.producer]
            if delivery.phase < source.start_phase:
                raise MappingProblemError("source is consumed before its availability begins")
            expected_kind, transport_start = _source_delivery(source, delivery.phase)

        if delivery.kind is not expected_kind or delivery.transport_start_phase != transport_start:
            raise MappingProblemError("planned delivery kind disagrees with producer availability")
        if transport_start is not None:
            current = expected_transport.setdefault(use.producer, (transport_start, []))
            if current[0] != transport_start:
                raise MappingProblemError("one producer acquired inconsistent transport anchors")
            current[1].append(delivery.phase)

    expected_lifetimes = tuple(
        sorted(
            (
                ExactLifetime(
                    producer=producer,
                    start_phase=start,
                    end_phase=max(taps),
                    tap_phases=tuple(sorted(set(taps))),
                )
                for producer, (start, taps) in expected_transport.items()
            ),
            key=lambda item: (item.start_phase, item.end_phase, item.producer),
        )
    )
    actual_lifetimes = tuple(
        sorted(
            plan.exact_lifetimes,
            key=lambda item: (item.start_phase, item.end_phase, item.producer),
        )
    )
    if actual_lifetimes != expected_lifetimes:
        raise MappingProblemError("realization plan exact lifetimes disagree with deliveries")

    transport_cost = sum(item.length for item in expected_lifetimes)
    if plan.entity_cost != entity_cost:
        raise MappingProblemError("realization plan entity cost is inconsistent")
    if plan.transport_cost != transport_cost:
        raise MappingProblemError("realization plan transport cost is inconsistent")


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
        raise MappingProblemError(f"operations have no implementation candidates: {sorted(missing)}")


def _candidates_by_operation(
    problem: MappingProblem,
    candidates: tuple[ImplementationCandidate, ...],
) -> dict[int, tuple[ImplementationCandidate, ...]]:
    return {
        operation.id: tuple(item for item in candidates if item.operation == operation.id)
        for operation in problem.operations
    }


def _transport_anchor(source: MappingSource) -> int | None:
    if source.mode is MappingSourceMode.EXACT:
        return source.start_phase
    return source.last_free_phase


def _source_delivery(source: MappingSource, phase: int) -> tuple[DeliveryKind, int | None]:
    last_free = source.last_free_phase
    if last_free is None or phase <= last_free:
        kind = (
            DeliveryKind.OBSERVE_AT
            if source.mode is MappingSourceMode.OBSERVABLE
            else DeliveryKind.REUSE
        )
        return kind, None
    return DeliveryKind.PRIVATE_TRANSPORT, last_free


def _extract_plan(
    problem: MappingProblem,
    candidates: tuple[ImplementationCandidate, ...],
    candidates_by_operation: dict[int, tuple[ImplementationCandidate, ...]],
    choose: dict[int, Any],
    output_phase: dict[int, Any],
    use_phase: dict[MappingUse, Any],
    solver: Any,
) -> RealizationPlan:
    realizations: list[SelectedRealization] = []
    output_values: dict[int, int] = {}
    for operation in problem.operations:
        selected = next(
            item
            for item in candidates_by_operation[operation.id]
            if solver.Value(choose[item.id]) == 1
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
            kind, transport_start = _source_delivery(sources[use.producer], phase)

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
    entity_cost = sum(item.entity_cost for item in realizations)
    transport_cost = sum(item.length for item in exact_lifetimes)
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
        entity_cost=entity_cost,
        transport_cost=transport_cost,
    )
