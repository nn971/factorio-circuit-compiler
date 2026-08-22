"""Serialize a final physical-synthesis Layout to Factorio 2.x blueprint data."""

from __future__ import annotations

import base64
import json
import zlib
from typing import Any

from factorio_circuit.ir.physical import (
    ArithmeticCombinator,
    ConstantCombinator,
    DeciderCombinator,
    Operand,
    SelectorCombinator,
    SignalId,
    WireColor,
)
from factorio_circuit.synthesis.layout import Layout
from factorio_circuit.target.factorio.decider import FACTORIO_COMPARATOR


def layout_to_blueprint_json(layout: Layout) -> dict[str, Any]:
    """Serialize an already-final layout without making placement/routing decisions."""

    entities: list[dict[str, Any]] = []
    for entity in layout.circuit.entities:
        x, y = layout.positions[entity.id]
        common: dict[str, Any] = {
            "entity_number": entity.id,
            "position": {"x": x, "y": y},
        }
        if entity.description:
            common["player_description"] = entity.description

        if isinstance(entity, SelectorCombinator):
            common.update(
                {
                    "name": "selector-combinator",
                    "direction": 4,
                    "control_behavior": _selector_conditions(entity),
                }
            )
        elif isinstance(entity, ArithmeticCombinator) and entity.operation == "select":
            # Compatibility for the existing vector-select lowering. New target providers should
            # emit the first-class SelectorCombinator above.
            common.update(
                {
                    "name": "selector-combinator",
                    "direction": 4,
                    "control_behavior": _legacy_selector_conditions(entity),
                }
            )
        elif isinstance(entity, ArithmeticCombinator):
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
        else:
            common["name"] = "constant-combinator"
            if entity.signals:
                common["control_behavior"] = _constant_behavior(entity)
        entities.append(common)

    for relay in layout.relays:
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
        "label": layout.name,
        "version": 562949955518464,
        "entities": entities,
    }
    wires = sorted({wire.as_factorio_tuple() for wire in layout.wires})
    if wires:
        blueprint["wires"] = [list(item) for item in wires]
    return {"blueprint": blueprint}


def encode_layout_blueprint_string(layout: Layout) -> str:
    payload = json.dumps(layout_to_blueprint_json(layout), separators=(",", ":")).encode()
    compressed = zlib.compress(payload, level=9)
    return "0" + base64.b64encode(compressed).decode("ascii")


def _selector_conditions(entity: SelectorCombinator) -> dict[str, Any]:
    if entity.operation == "select":
        return {
            "operation": "select",
            "select_max": entity.select_max,
            "index_constant": entity.index,
        }
    if entity.operation == "random":
        return {
            "operation": "random",
            "random_update_interval": entity.random_update_interval,
        }
    raise ValueError(f"unsupported selector operation {entity.operation!r}")


def _legacy_selector_conditions(entity: ArithmeticCombinator) -> dict[str, Any]:
    if entity.right.constant is None:
        raise ValueError("selector scaffold requires a constant index")
    return {
        "operation": "select",
        "select_max": True,
        "index_constant": entity.right.constant,
    }


def _arithmetic_conditions(entity: ArithmeticCombinator) -> dict[str, Any]:
    result: dict[str, Any] = {"operation": _factorio_operation(entity.operation)}
    _encode_operand(result, "first", entity.left)
    _encode_operand(result, "second", entity.right)
    if entity.output_each:
        result["output_signal"] = _signal_json(SignalId("virtual", "signal-each"))
    elif entity.output_signal is not None:
        result["output_signal"] = _signal_json(entity.output_signal)
    else:  # pragma: no cover
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
    outputs = [
        _decider_output(
            entity.output_signal,
            copy_count=entity.output_copy_count_from_input,
            constant=entity.output_constant,
            networks=entity.output_networks,
        )
    ]
    outputs.extend(
        _decider_output(
            output.signal,
            copy_count=output.copy_count_from_input,
            constant=output.constant,
            networks=output.output_networks,
        )
        for output in entity.additional_outputs
    )
    if entity.else_output_signal is not None:
        raise ValueError("the current Factorio target does not support decider else outputs")
    return {"conditions": conditions, "outputs": outputs}


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
            "quality": "normal",
            "comparator": "=",
            "count": count,
        }
        if signal.kind:
            item["type"] = signal.kind
        filters.append(item)
    return {"sections": {"sections": [{"index": 1, "filters": filters}]}}


def _encode_operand(
    result: dict[str, Any],
    prefix: str,
    operand: Operand,
    *,
    decider_second: bool = False,
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
    result: dict[str, Any],
    key: str,
    networks: tuple[WireColor, ...] | None,
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
