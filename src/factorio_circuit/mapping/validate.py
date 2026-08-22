"""Independent validation for selected temporal technology-mapping plans."""

from __future__ import annotations

from .candidate_coupling import validate_selected_candidate_coupling
from .plan import (
    DeliveryKind,
    ExactLifetime,
    RealizationPlan,
    SelectedRealization,
    WireSumResource,
)
from .problem import (
    MappingProblem,
    MappingProblemError,
    MappingSource,
    MappingSourceMode,
    MappingUse,
)
from .templates import CandidateOutputMode, ImplementationCandidate, ImplementationKind


def validate_realization_plan(
    problem: MappingProblem,
    candidates: tuple[ImplementationCandidate, ...],
    plan: RealizationPlan,
) -> None:
    """Validate coverage, candidate timing, availability, resources, and plan costs."""

    candidate_by_id = {item.id: item for item in candidates}
    if len(candidate_by_id) != len(candidates):
        raise MappingProblemError("mapping candidates must have unique ids")
    validate_selected_candidate_coupling(candidates, plan)

    realization_by_operation = {item.operation: item for item in plan.realizations}
    if len(realization_by_operation) != len(plan.realizations):
        raise MappingProblemError("realization plan contains duplicate operation realizations")
    expected_operations = {item.id for item in problem.operations}
    if set(realization_by_operation) != expected_operations:
        raise MappingProblemError(
            "realization plan must realize every semantic operation exactly once"
        )

    deliveries = {
        MappingUse(item.producer, item.consumer, item.operand_index): item
        for item in plan.deliveries
    }
    if len(deliveries) != len(plan.deliveries):
        raise MappingProblemError("realization plan contains duplicate semantic deliveries")
    if set(deliveries) != set(problem.uses()):
        raise MappingProblemError("realization plan must satisfy every semantic use exactly once")

    operations = {item.id: item for item in problem.operations}
    sources = {item.id: item for item in problem.sources}
    entity_cost = 0
    selected_candidate: dict[int, ImplementationCandidate] = {}

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
        selected_candidate[operation_id] = candidate
        entity_cost += realization.entity_cost

        operation = operations[operation_id]
        for operand_index, offset in enumerate(candidate.input_phase_offsets):
            use = MappingUse(operation.operands[operand_index], operation_id, operand_index)
            expected_phase = realization.output_phase + offset
            if expected_phase < 0 or deliveries[use].phase != expected_phase:
                raise MappingProblemError(
                    "candidate timing equation disagrees with planned delivery"
                )

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
            transport_start = start if delivery.phase > start else None
            free_kind = DeliveryKind.REUSE
        else:
            source = sources[use.producer]
            if delivery.phase < source.start_phase:
                raise MappingProblemError("source is consumed before its availability begins")
            source_kind, transport_start = source_delivery_kind(source, delivery.phase)
            free_kind = source_kind

        if transport_start is None:
            if delivery.kind is not free_kind or delivery.transport_start_phase is not None:
                raise MappingProblemError(
                    "planned free delivery disagrees with producer availability"
                )
            continue

        if delivery.kind not in {DeliveryKind.PRIVATE_TRANSPORT, DeliveryKind.BUS_TRANSPORT}:
            raise MappingProblemError(
                "exact transport use is missing a physical transport mechanism"
            )
        if delivery.transport_start_phase != transport_start:
            raise MappingProblemError(
                "planned transport start disagrees with producer availability"
            )
        current = expected_transport.setdefault(use.producer, (transport_start, []))
        if current[0] != transport_start:
            raise MappingProblemError("one producer acquired inconsistent transport anchors")
        current[1].append(delivery.phase)

    expected_lifetimes = _lifetimes_from_transport(expected_transport)
    actual_lifetimes = tuple(
        sorted(
            plan.exact_lifetimes,
            key=lambda item: (item.start_phase, item.end_phase, item.producer),
        )
    )
    if actual_lifetimes != expected_lifetimes:
        raise MappingProblemError("realization plan exact lifetimes disagree with deliveries")

    _validate_wire_sums(
        problem,
        selected_candidate,
        realization_by_operation,
        plan.wire_sums,
    )
    bus_producers = _validate_delay_buses(plan, expected_lifetimes)

    private_transport_cost = sum(
        item.length for item in expected_lifetimes if item.producer not in bus_producers
    )
    bus_transport_cost = sum(
        bus.middle_stages + bus.interface_combinators for bus in plan.delay_buses
    )
    transport_cost = private_transport_cost + bus_transport_cost
    if plan.entity_cost != entity_cost:
        raise MappingProblemError("realization plan entity cost is inconsistent")
    if plan.transport_cost != transport_cost:
        raise MappingProblemError("realization plan transport cost is inconsistent")


def source_delivery_kind(
    source: MappingSource,
    phase: int,
) -> tuple[DeliveryKind, int | None]:
    """Classify one source use under its implementation-independent observation contract."""

    last_free = source.last_free_phase
    if last_free is None or phase <= last_free:
        kind = (
            DeliveryKind.OBSERVE_AT
            if source.mode is MappingSourceMode.OBSERVABLE
            else DeliveryKind.REUSE
        )
        return kind, None
    return DeliveryKind.PRIVATE_TRANSPORT, last_free


def transport_anchor(source: MappingSource) -> int | None:
    """Return the phase where residual exact transport begins, if one can be required."""

    if source.mode is MappingSourceMode.EXACT:
        return source.start_phase
    return source.last_free_phase


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


def _validate_delay_buses(
    plan: RealizationPlan,
    lifetimes: tuple[ExactLifetime, ...],
) -> set[int]:
    lifetime_by_producer = {item.producer: item for item in lifetimes}
    if len(lifetime_by_producer) != len(lifetimes):
        raise MappingProblemError("exact lifetimes must have unique producers")

    bus_indices = [bus.index for bus in plan.delay_buses]
    if len(set(bus_indices)) != len(bus_indices):
        raise MappingProblemError("delay-bus resource indices must be unique")

    bus_delivery_phases: dict[int, list[int]] = {}
    private_transport_producers: set[int] = set()
    for delivery in plan.deliveries:
        if delivery.kind is DeliveryKind.BUS_TRANSPORT:
            bus_delivery_phases.setdefault(delivery.producer, []).append(delivery.phase)
        elif delivery.kind is DeliveryKind.PRIVATE_TRANSPORT:
            private_transport_producers.add(delivery.producer)

    bus_producers: set[int] = set()
    for bus in plan.delay_buses:
        if len(bus.lanes) < 2:
            raise MappingProblemError("an active delay bus must contain at least two lanes")
        if bus.middle_end_phase < bus.middle_start_phase:
            raise MappingProblemError("delay-bus middle span cannot run backwards")

        expected_middle_start = min(lane.ingress_phase for lane in bus.lanes)
        expected_middle_end = max(lane.trunk_end_phase for lane in bus.lanes)
        if (bus.middle_start_phase, bus.middle_end_phase) != (
            expected_middle_start,
            expected_middle_end,
        ):
            raise MappingProblemError("delay-bus middle span disagrees with its lane lifetimes")

        for lane in bus.lanes:
            if lane.producer in bus_producers:
                raise MappingProblemError("one producer cannot be assigned to multiple delay buses")
            bus_producers.add(lane.producer)
            lifetime = lifetime_by_producer.get(lane.producer)
            if lifetime is None:
                raise MappingProblemError("delay-bus lane has no corresponding exact lifetime")
            if (lane.start_phase, lane.end_phase) != (
                lifetime.start_phase,
                lifetime.end_phase,
            ):
                raise MappingProblemError("delay-bus lane span disagrees with exact lifetime")
            if lane.end_phase - lane.start_phase < 3:
                raise MappingProblemError(
                    "delay-bus lane requires an exact lifetime of at least 3 ticks"
                )
            expected_phases = tuple(sorted(bus_delivery_phases.get(lane.producer, ())))
            if tuple(sorted(lane.delivery_phases)) != expected_phases:
                raise MappingProblemError("delay-bus lane interfaces disagree with bus deliveries")
            if not expected_phases:
                raise MappingProblemError("delay-bus lane has no transported semantic use")

    if set(bus_delivery_phases) != bus_producers:
        raise MappingProblemError("BUS_TRANSPORT deliveries must match delay-bus lane producers")
    if bus_producers & private_transport_producers:
        raise MappingProblemError(
            "one exact lifetime cannot mix private and bus transport in the first bus model"
        )
    return bus_producers


def _validate_wire_sums(
    problem: MappingProblem,
    selected: dict[int, ImplementationCandidate],
    realizations: dict[int, SelectedRealization],
    wire_sums: tuple[WireSumResource, ...],
) -> None:
    operations = {item.id: item for item in problem.operations}
    by_operation = {item.operation: item for item in wire_sums}
    if len(by_operation) != len(wire_sums):
        raise MappingProblemError("realization plan contains duplicate wire-sum resources")

    expected = {
        operation_id
        for operation_id, candidate in selected.items()
        if candidate.kind is ImplementationKind.WIRE_SUM
    }
    if set(by_operation) != expected:
        raise MappingProblemError("wire-sum resources must match selected wire-sum candidates")

    for operation_id in expected:
        operation = operations[operation_id]
        if len(operation.operands) != 2:
            raise MappingProblemError("wire-sum resource requires two semantic operands")
        resource = by_operation[operation_id]
        realization = realizations[operation_id]
        if (
            resource.left_producer != operation.operands[0]
            or resource.right_producer != operation.operands[1]
            or resource.phase != realization.output_phase
        ):
            raise MappingProblemError("wire-sum resource disagrees with its selected realization")
        for producer in operation.operands:
            producer_realization = realizations.get(producer)
            if producer_realization is None:
                raise MappingProblemError("wire-sum contribution must be an operation result")
            if producer_realization.output_phase != resource.phase:
                raise MappingProblemError(
                    "first-milestone wire-sum contributors must be produced on the shared phase"
                )
