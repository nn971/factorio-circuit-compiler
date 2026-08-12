"""Encode a ready-to-layout physical circuit as Factorio 2.x blueprint JSON/string."""

from __future__ import annotations

import base64
import json
import zlib
from typing import Any

from factorio_circuit.blueprint.layout import row_positions
from factorio_circuit.blueprint.routing import (
    DEFAULT_SAFE_WIRE_SPAN,
    route_wires,
    routed_positions,
    validate_wire_spans,
)
from factorio_circuit.blueprint.wiring import blueprint_wires
from factorio_circuit.ir.physical import (
    ArithmeticCombinator,
    ConstantCombinator,
    DeciderCombinator,
    Operand,
    PhysicalCircuit,
    SignalId,
    WireColor,
)
from factorio_circuit.target.factorio.decider import FACTORIO_COMPARATOR


def to_blueprint_json(
    circuit: PhysicalCircuit, *, safe_wire_span: float = DEFAULT_SAFE_WIRE_SPAN
) -> dict[str, Any]:
    positions = row_positions(circuit)
    routing = route_wires(circuit, positions, safe_span=safe_wire_span)
    routed = routed_positions(circuit, positions, routing)
    validate_wire_spans(routing.wires, routed, maximum_span=safe_wire_span)
    entities: list[dict[str, Any]] = []
    for entity in circuit.entities:
        x, y = positions[entity.id]
        common: dict[str, Any] = {
            "entity_number": entity.id,
            "position": {"x": x, "y": y},
        }
        if entity.description:
            common["player_description"] = entity.description

        if isinstance(entity, ArithmeticCombinator):
            common.update(
                {
                    "name": "arithmetic-combinator",
                    "direction": 4,
                    "control_behavior": {
                        "arithmetic_conditions": _arithmetic_conditions(entity),
                    },
                }
            )
        elif isinstance(entity, DeciderCombinator):
            common.update(
                {
                    "name": "decider-combinator",
                    "direction": 4,
                    "control_behavior": {
                        "decider_conditions": _decider_conditions(entity),
                    },
                }
            )
        elif isinstance(entity, ConstantCombinator):
            common["name"] = "constant-combinator"
            if entity.signals:
                common["control_behavior"] = _constant_behavior(entity)
        else:  # pragma: no cover
            raise TypeError(entity)
        entities.append(common)

    for relay in routing.relays:
        x, y = relay.position
        entities.append(
            {
                "entity_number": relay.entity_id,
                "name": "constant-combinator",
                "position": {"x": x, "y": y},
                "player_description": relay.description,
            }
        )

    blueprint: dict[str, Any] = {
        "item": "blueprint",
        "label": circuit.name,
        "version": 562949955518464,
        "entities": entities,
    }
    wires = blueprint_wires(routing)
    if wires:
        blueprint["wires"] = [list(item) for item in wires]
    return {"blueprint": blueprint}


def encode_blueprint_string(
    circuit: PhysicalCircuit, *, safe_wire_span: float = DEFAULT_SAFE_WIRE_SPAN
) -> str:
    payload = json.dumps(
        to_blueprint_json(circuit, safe_wire_span=safe_wire_span), separators=(",", ":")
    ).encode()
    compressed = zlib.compress(payload, level=9)
    return "0" + base64.b64encode(compressed).decode("ascii")


def _arithmetic_conditions(entity: ArithmeticCombinator) -> dict[str, Any]:
    result: dict[str, Any] = {"operation": _factorio_operation(entity.operation)}
    _encode_operand(result, "first", entity.left)
    _encode_operand(result, "second", entity.right)
    if entity.output_each:
        result["output_signal"] = _signal_json(SignalId("virtual", "signal-each"))
    elif entity.output_signal is not None:
        result["output_signal"] = _signal_json(entity.output_signal)
    else:
        raise ValueError("arithmetic combinator has no output")
    return result


def _decider_conditions(entity: DeciderCombinator) -> dict[str, Any]:
    conditions = [_decider_condition(entity.comparator, entity.left, entity.right)]
    conditions.extend(
        _decider_condition(
            condition.comparator,
            condition.left,
            condition.right,
            compare_type=condition.compare_type,
        )
        for condition in entity.additional_conditions
    )
    output = _decider_output(
        entity.output_signal,
        copy_count=entity.output_copy_count_from_input,
        constant=entity.output_constant,
        networks=entity.output_networks,
    )
    if entity.else_output_signal is not None:
        raise ValueError("the current Factorio target does not support decider else outputs")
    return {"conditions": conditions, "outputs": [output]}


def _decider_condition(
    comparator: str,
    left: Operand,
    right: Operand,
    *,
    compare_type: str | None = None,
) -> dict[str, Any]:
    condition: dict[str, Any] = {"comparator": FACTORIO_COMPARATOR[comparator]}
    _encode_operand(condition, "first", left)
    _encode_operand(condition, "second", right, decider_second=True)
    if compare_type is not None:
        condition["compare_type"] = compare_type
    return condition


def _decider_output(
    signal: SignalId,
    *,
    copy_count: bool,
    constant: int,
    networks: tuple[WireColor, ...] | None,
) -> dict[str, Any]:
    output: dict[str, Any] = {
        "signal": _signal_json(signal),
        "copy_count_from_input": copy_count,
    }
    if copy_count:
        _encode_network_selection(output, "networks", networks)
    elif constant != 1:
        output["constant"] = constant
    return output


def _constant_behavior(entity: ConstantCombinator) -> dict[str, Any]:
    filters: list[dict[str, Any]] = []
    for index, (signal, count) in enumerate(entity.signals, start=1):
        item: dict[str, Any] = {
            "index": index,
            "name": signal.name,
            # Factorio 2.x interprets an omitted quality as the special
            # "any quality" selector. Constants in our IR are ordinary
            # signals, so serialize that semantic explicitly.
            "quality": "normal",
            "comparator": "=",
            "count": count,
        }
        if signal.kind:
            item["type"] = signal.kind
        filters.append(item)
    return {"sections": {"sections": [{"index": 1, "filters": filters}]}}


def _encode_operand(
    result: dict[str, Any], prefix: str, operand: Operand, *, decider_second: bool = False
) -> None:
    if operand.each:
        result[f"{prefix}_signal"] = _signal_json(SignalId("virtual", "signal-each"))
    elif operand.signal is not None:
        result[f"{prefix}_signal"] = _signal_json(operand.signal)
    elif operand.constant is not None:
        key = "constant" if decider_second and prefix == "second" else f"{prefix}_constant"
        result[key] = operand.constant
    else:  # pragma: no cover
        raise ValueError("invalid operand")
    if operand.signal is not None or operand.each:
        _encode_network_selection(result, f"{prefix}_signal_networks", operand.networks)


def _encode_network_selection(
    result: dict[str, Any], key: str, networks: tuple[WireColor, ...] | None
) -> None:
    if networks is None:
        return
    selected = set(networks)
    result[key] = {
        "red": WireColor.RED in selected,
        "green": WireColor.GREEN in selected,
    }


def _signal_json(signal: SignalId) -> dict[str, str]:
    return {"type": signal.kind, "name": signal.name}


def _factorio_operation(operation: str) -> str:
    return {
        "//": "/",
        "**": "^",
        "&": "AND",
        "|": "OR",
        "^": "XOR",
    }.get(operation, operation)
