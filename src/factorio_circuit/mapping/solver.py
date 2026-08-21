"""Joint candidate selection and physical-phase optimization for temporal technology mapping.

The solver owns target timing: semantic operations do not arrive with precomputed physical latency
windows. Each selected implementation candidate contributes its own input/output phase equations.
The first milestone supports finite local candidates, free source reuse/observation, prefix-shared
private exact transport, and a deliberately narrow zero-delay wire-sum candidate. Shared delay buses
and other parameterized resource families are intentionally deferred.
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

    The objective is abstract implementation entities plus private exact-transport stages. Source
    ``STABLE``/``OBSERVABLE`` windows are free through their last valid phase; later uses begin one
    exact lifetime at that boundary. Operation outputs are conservatively EXACT in this milestone.
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
                constraint = use_phase[use] == output_phase[operation.id] + offset
                model.Add(constraint).OnlyEnforceIf(choose[candidate.id])

            if candidate.kind is ImplementationKind.WIRE_SUM:
                # The first physical wire-sum realization is deliberately strict: both producer
                # output connectors themselves drive the same carrier on the same physical tick.
                # Later contribution-port/resource modeling may allow a preceding transport stage
                # to join the aggregation network instead.
                for producer in operation.operands:
                    if producer not in operations:
                        raise MappingProblemError(
                            "wire-sum candidate requires operation-result operands"
                        )
                    model.Add(
                        output_phase[producer] == output_phase[operation.id]
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
        anchor = transport_anchor(source)
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
        solver,
    )
    validate_realization_plan(problem, selected_candidates, plan)
    return MappingOptimizationResult(
        status=status,
        plan=plan,
        wall_time_seconds=float(solver.WallTime()),
    )


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


def _extract_plan(
    problem: MappingProblem,
    candidates_by_operation: dict[int, tuple[ImplementationCandidate, ...]],
    choose: dict[int, Any],
    output_phase: dict[int, Any],
    use_phase: dict[MappingUse, Any],
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
        wire_sums=wire_sums,
        entity_cost=entity_cost,
        transport_cost=transport_cost,
    )
