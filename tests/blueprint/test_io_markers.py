from factorio_circuit import Circuit, compile_circuit


def test_blueprint_contains_described_io_markers_decider_and_wires() -> None:
    c = Circuit("branch")
    a = c.input("a")
    b = c.input("b")
    x = (a >= b).select(a * 2, b * 2)
    c.output("x", x)
    result = compile_circuit(c)

    blueprint = result.blueprint_json["blueprint"]
    entities = blueprint["entities"]

    descriptions = [entity.get("player_description", "") for entity in entities]
    assert any(text.endswith("INPUT a — inject value on [signal-A] here") for text in descriptions)
    assert any(text.endswith("INPUT b — inject value on [signal-B] here") for text in descriptions)
    assert any(text.startswith("[FCC #") and "OUTPUT x" in text for text in descriptions)
    assert any(entity["name"] == "decider-combinator" for entity in entities)
    assert blueprint["wires"]

    marker_count = sum(" | marker] " in text for text in descriptions)
    assert len(entities) >= result.physical_circuit.blueprint_entity_count
    assert (
        result.physical_circuit.blueprint_entity_count
        >= result.physical_circuit.combinator_count + marker_count
    )
