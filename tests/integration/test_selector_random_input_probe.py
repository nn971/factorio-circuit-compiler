from __future__ import annotations

from benchmarks.selectors.random_input_probe import build_probe
from factorio_circuit.ir.physical import SelectorCombinator


def test_random_input_probe_materializes_configured_selector() -> None:
    result = build_probe(update_interval=7)

    selectors = [
        entity
        for entity in result.physical_circuit.entities
        if isinstance(entity, SelectorCombinator)
    ]
    assert len(selectors) == 1
    assert selectors[0].operation == "random"
    assert selectors[0].random_update_interval == 7
    assert [port.name for port in result.physical_circuit.inputs] == ["candidates"]
    assert [port.name for port in result.physical_circuit.outputs] == ["choice"]

    blueprint = result.blueprint_json["blueprint"]
    assert isinstance(blueprint, dict)
    encoded = [
        entity
        for entity in blueprint["entities"]
        if isinstance(entity, dict) and entity.get("name") == "selector-combinator"
    ]
    assert len(encoded) == 1
    assert encoded[0]["control_behavior"] == {
        "operation": "random",
        "random_update_interval": 7,
    }
