"""Reference materialization for semantic circuit output declarations."""

from __future__ import annotations

from dataclasses import dataclass

from factorio_circuit.events import EventMaterializationError
from factorio_circuit.ir.output import (
    OutputMaterialization,
    OutputMaterializationPolicy,
    materialized_return_value,
)
from factorio_circuit.ir.semantic import (
    CircuitModule,
    EventInput,
    EventScalarFlow,
    EventVectorFlow,
    PayloadShape,
    SampleOn,
    TemporalModality,
    is_vector_value,
    validate_expression_flow,
)

from .events import (
    EventPayload,
    EventSimulationResult,
    TimestampDomain,
    _evaluate_scalar,
    _evaluate_vector,
)


@dataclass(frozen=True, slots=True)
class MaterializedOutputTrace:
    """Dense reference trace for one declared sparse circuit output."""

    name: str
    reference: object
    payload_shape: PayloadShape
    contract: OutputMaterialization
    domain: TimestampDomain
    payloads: tuple[EventPayload, ...]
    valid: tuple[bool, ...] | None = None

    @property
    def valid_name(self) -> str | None:
        return self.contract.valid_name


def _resolved_output_name(module: CircuitModule, index: int) -> str:
    if module.output.names:
        declared = module.output.names[index]
        if declared:
            return declared
    candidate = getattr(module.output.values[index], "name", None)
    return candidate or f"out{index}"


def _resolve_output_index(module: CircuitModule, output: str | int) -> int:
    if isinstance(output, bool):
        raise EventMaterializationError("output selector must be a name or non-negative index")
    if isinstance(output, int):
        if output < 0 or output >= len(module.output.values):
            raise EventMaterializationError("output index is outside the declared output range")
        return output
    if isinstance(output, str):
        matches = [
            index
            for index in range(len(module.output.values))
            if _resolved_output_name(module, index) == output
        ]
        if len(matches) != 1:
            raise EventMaterializationError(f"output {output!r} is not uniquely declared")
        return matches[0]
    raise EventMaterializationError("output selector must be a name or non-negative index")


def _event_sources(value: object, seen: set[int] | None = None) -> set[EventInput]:
    if seen is None:
        seen = set()
    if id(value) in seen:
        return set()
    seen.add(id(value))
    if isinstance(value, (EventScalarFlow, EventVectorFlow)):
        return {value.source}
    if isinstance(value, SampleOn):
        return {value.target}

    result: set[EventInput] = set()
    for field_name in (
        "left",
        "right",
        "condition",
        "when_true",
        "when_false",
        "vector",
        "scalar",
    ):
        child = getattr(value, field_name, None)
        if child is not None:
            result.update(_event_sources(child, seen))
    return result


def _empty_payload(shape: PayloadShape) -> EventPayload:
    return 0 if shape is PayloadShape.SCALAR else {}


def _copy_payload(payload: EventPayload) -> EventPayload:
    return dict(payload) if isinstance(payload, dict) else payload


def materialize_output_trace(
    result: EventSimulationResult,
    module: CircuitModule,
    output: str | int,
) -> MaterializedOutputTrace:
    """Materialize one declared Event output according to its boundary contract.

    Internal Event values remain sparse.  This function is the reference implementation of the
    explicit output policy: it evaluates the Event expression only on its occurrence clock and then
    projects those occurrences onto the dense timestamp domain using HOLD, ZERO, or VALID.
    """

    index = _resolve_output_index(module, output)
    declared = materialized_return_value(module.output)
    value = declared.values[index]
    contract = declared.contract_for(index)
    facts = validate_expression_flow(value)
    if facts.modality is not TemporalModality.EVENT:
        raise EventMaterializationError(
            "materialize_output_trace currently requires an Event-shaped declared output"
        )

    sources = _event_sources(value)
    if len(sources) != 1:
        raise EventMaterializationError(
            "declared Event output must have exactly one recoverable occurrence source"
        )
    source = next(iter(sources))
    if source not in result.event_inputs:
        raise EventMaterializationError(
            "declared Event output source is not part of this simulation result"
        )

    shape = PayloadShape.VECTOR if is_vector_value(value) else PayloadShape.SCALAR
    updates: dict[int, EventPayload] = {}
    for reaction in result.reactions:
        activation = next(
            (item for item in reaction.activations if item.source == source),
            None,
        )
        if activation is None:
            continue
        if reaction.timestamp in updates:
            raise EventMaterializationError("declared output has duplicate timestamp data")
        if shape is PayloadShape.VECTOR:
            payload: EventPayload = _evaluate_vector(
                value,
                reaction.level_row,
                reaction.state_before,
                source,
                activation.payload,
            )
        else:
            payload = _evaluate_scalar(
                value,
                reaction.level_row,
                reaction.state_before,
                source,
                activation.payload,
            )
        updates[reaction.timestamp] = _copy_payload(payload)

    empty = _empty_payload(shape)
    current = _copy_payload(empty)
    rows: list[EventPayload] = []
    valid: list[bool] = []
    for timestamp in range(result.domain.start, result.domain.stop):
        present = timestamp in updates
        if present:
            current = _copy_payload(updates[timestamp])
        elif contract.policy in (
            OutputMaterializationPolicy.ZERO,
            OutputMaterializationPolicy.VALID,
        ):
            current = _copy_payload(empty)
        rows.append(_copy_payload(current))
        valid.append(present)

    return MaterializedOutputTrace(
        name=_resolved_output_name(module, index),
        reference=value,
        payload_shape=shape,
        contract=contract,
        domain=result.domain,
        payloads=tuple(rows),
        valid=tuple(valid) if contract.policy is OutputMaterializationPolicy.VALID else None,
    )


__all__ = [
    "MaterializedOutputTrace",
    "materialize_output_trace",
]
