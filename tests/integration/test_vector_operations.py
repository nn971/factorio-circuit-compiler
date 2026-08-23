"""Integration coverage for whole-vector operations."""

from typing import Any, cast

from factorio_circuit import SignalId, compile_circuit
from factorio_circuit.frontend import Circuit
from factorio_circuit.ir.physical import DeciderCombinator, SelectorCombinator
from factorio_circuit.simulate.physical import simulate_stream

IRON = SignalId("item", "iron-plate")
COPPER = SignalId("item", "copper-plate")
COAL = SignalId("item", "coal")
STONE = SignalId("item", "stone")
ANYTHING = SignalId("virtual", "signal-anything")
EACH = SignalId("virtual", "signal-each")


def _vector_circuit() -> Circuit:
    c = Circuit("vector_operations")
    left = c.signals("left")
    right = c.signals("right")
    scale = c.input("scale")
    enable = c.input("enable")

    difference = left - right
    c.output("sum", left + right)
    c.output("difference", difference)
    c.output("scaled", difference * scale)
    c.output("negated", -left)
    c.output("gated", difference.gate(enable))
    c.output("positive", difference.positive())
    c.output("any", difference.any())
    return c


def _max_circuit() -> Circuit:
    c = Circuit("vector_max")
    values = c.signals("values")
    c.output("maximum", values.max())
    return c


def _selector_circuit() -> Circuit:
    c = Circuit("vector_selectors")
    values = c.signals("values")
    c.output("minimum", values.min())
    c.output("second_largest", values.select(1))
    c.output("second_smallest", values.select(1, descending=False))
    return c


def _value_at(
    observations: list[tuple[object, ...]],
    result: Any,
    logical_tick: int,
    name: str,
) -> object:
    index = next(i for i, port in enumerate(result.physical_circuit.outputs) if port.name == name)
    port = result.physical_circuit.outputs[index]
    return observations[logical_tick + port.phase][index]


def test_vector_arithmetic_matches_sparse_map_semantics() -> None:
    result = compile_circuit(_vector_circuit())
    stream = [
        {
            "left": {IRON: 7, COPPER: -3, COAL: 4},
            "right": {IRON: -2, COPPER: 3, STONE: 9},
            "scale": -2,
            "enable": -7,
        },
        {
            "left": {IRON: 5, COPPER: 1},
            "right": {IRON: 5, COPPER: 1},
            "scale": 3,
            "enable": 0,
        },
    ]
    observations = simulate_stream(result.physical_circuit, stream)

    assert _value_at(observations, result, 0, "sum") == {
        IRON: 5,
        COAL: 4,
        STONE: 9,
    }
    assert _value_at(observations, result, 0, "difference") == {
        IRON: 9,
        COPPER: -6,
        COAL: 4,
        STONE: -9,
    }
    assert _value_at(observations, result, 0, "scaled") == {
        IRON: -18,
        COPPER: 12,
        COAL: -8,
        STONE: 18,
    }
    assert _value_at(observations, result, 0, "negated") == {
        IRON: -7,
        COPPER: 3,
        COAL: -4,
    }
    assert _value_at(observations, result, 0, "gated") == {
        IRON: 9,
        COPPER: -6,
        COAL: 4,
        STONE: -9,
    }

    assert _value_at(observations, result, 1, "difference") == {}
    assert _value_at(observations, result, 1, "scaled") == {}
    assert _value_at(observations, result, 1, "gated") == {}


def test_positive_and_any_lower_to_factorio_wildcards() -> None:
    result = compile_circuit(_vector_circuit())
    deciders = [
        entity
        for entity in result.physical_circuit.entities
        if isinstance(entity, DeciderCombinator)
    ]

    positive = next(
        entity for entity in deciders if entity.description == "runtime vector positive filter"
    )
    assert positive.left.each
    assert positive.comparator == ">"
    assert positive.right.constant == 0
    assert positive.output_signal == EACH
    assert positive.output_copy_count_from_input

    anything = next(entity for entity in deciders if entity.left.signal == ANYTHING)
    assert anything.comparator == "!="
    assert anything.right.constant == 0

    blueprint = cast(dict[str, Any], result.blueprint_json["blueprint"])
    positive_json = next(
        entity
        for entity in cast(list[dict[str, Any]], blueprint["entities"])
        if entity.get("player_description", "").endswith("runtime vector positive filter")
    )
    output = positive_json["control_behavior"]["decider_conditions"]["outputs"][0]
    assert output["signal"]["name"] == "signal-each"
    assert output["copy_count_from_input"] is True


def test_max_lowers_to_one_factorio_selector_combinator() -> None:
    result = compile_circuit(_max_circuit())

    selector_entity = next(
        entity
        for entity in result.physical_circuit.entities
        if isinstance(entity, SelectorCombinator) and entity.description == "runtime vector max"
    )
    assert selector_entity.operation == "select"
    assert selector_entity.select_max is True
    assert selector_entity.index == 0
    maximum_port = next(port for port in result.physical_circuit.outputs if port.name == "maximum")
    assert maximum_port.phase == 1

    blueprint = cast(dict[str, Any], result.blueprint_json["blueprint"])
    selector = next(
        entity
        for entity in cast(list[dict[str, Any]], blueprint["entities"])
        if entity.get("player_description", "").endswith("runtime vector max")
    )
    assert selector["name"] == "selector-combinator"
    assert selector["control_behavior"] == {
        "operation": "select",
        "select_max": True,
        "index_constant": 0,
    }


def test_indexed_and_ascending_selectors_preserve_target_configuration() -> None:
    result = compile_circuit(_selector_circuit())

    selectors = [
        entity
        for entity in result.physical_circuit.entities
        if isinstance(entity, SelectorCombinator)
    ]
    assert {(entity.select_max, entity.index) for entity in selectors} == {
        (False, 0),
        (True, 1),
        (False, 1),
    }
    assert all(port.phase == 1 for port in result.physical_circuit.outputs)

    blueprint = cast(dict[str, Any], result.blueprint_json["blueprint"])
    encoded = [
        entity["control_behavior"]
        for entity in cast(list[dict[str, Any]], blueprint["entities"])
        if entity["name"] == "selector-combinator"
    ]
    assert {
        (item["select_max"], item["index_constant"])
        for item in encoded
        if item["operation"] == "select"
    } == {
        (False, 0),
        (True, 1),
        (False, 1),
    }
