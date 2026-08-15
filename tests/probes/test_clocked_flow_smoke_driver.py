from examples.clocked_flow_ingame_smoke import build_circuit, build_driver_blueprint
from factorio_circuit import compile_circuit
from probes.blueprint_utils import decode_blueprint, encode_blueprint


def _entity(payload: dict[str, object], entity_number: int) -> dict[str, object]:
    blueprint = payload["blueprint"]
    assert isinstance(blueprint, dict)
    entities = blueprint["entities"]
    assert isinstance(entities, list)
    for entity in entities:
        if isinstance(entity, dict) and entity.get("entity_number") == entity_number:
            return entity
    raise AssertionError(f"missing entity {entity_number}")


def _decider_phase(entity: dict[str, object]) -> int:
    control = entity["control_behavior"]
    assert isinstance(control, dict)
    decider = control["decider_conditions"]
    assert isinstance(decider, dict)
    conditions = decider["conditions"]
    assert isinstance(conditions, list)
    condition = conditions[0]
    assert isinstance(condition, dict)
    phase = condition["constant"]
    assert isinstance(phase, int)
    return phase


def _decider_output_signal(entity: dict[str, object]) -> dict[str, str]:
    control = entity["control_behavior"]
    assert isinstance(control, dict)
    decider = control["decider_conditions"]
    assert isinstance(decider, dict)
    outputs = decider["outputs"]
    assert isinstance(outputs, list)
    output = outputs[0]
    assert isinstance(output, dict)
    output_signal = output["signal"]
    assert isinstance(output_signal, dict)
    return output_signal


def test_clocked_flow_smoke_driver_matches_compiled_abi_and_schedule() -> None:
    compiled = compile_circuit(build_circuit())
    payload = build_driver_blueprint(compiled)

    encoded = encode_blueprint(payload)
    assert decode_blueprint(encoded) == payload

    blueprint = payload["blueprint"]
    assert isinstance(blueprint, dict)
    entities = blueprint["entities"]
    assert isinstance(entities, list)
    assert len(entities) == 18

    assert [_decider_phase(_entity(payload, entity)) for entity in (3, 4, 5)] == [20, 80, 140]
    assert [_decider_phase(_entity(payload, entity)) for entity in (6, 7, 8)] == [20, 80, 140]
    assert [_decider_phase(_entity(payload, entity)) for entity in (9, 10, 11)] == [80, 120, 200]
    assert [_decider_phase(_entity(payload, entity)) for entity in (12, 13)] == [80, 200]

    source_valid = compiled.physical_circuit.input_signals["source__valid"]
    tick_valid = compiled.physical_circuit.input_signals["tick__valid"]
    enabled = compiled.physical_circuit.input_signals["enabled"]

    assert _decider_output_signal(_entity(payload, 6)) == {
        "type": source_valid.kind,
        "name": source_valid.name,
    }
    assert _decider_output_signal(_entity(payload, 9)) == {
        "type": tick_valid.kind,
        "name": tick_valid.name,
    }
    assert _decider_output_signal(_entity(payload, 12)) == {
        "type": enabled.kind,
        "name": enabled.name,
    }

    descriptions = {
        entity["entity_number"]: entity.get("player_description", "")
        for entity in entities
        if isinstance(entity, dict)
    }
    assert "INPUT source" in descriptions[14]
    assert "INPUT source__valid" in descriptions[15]
    assert "INPUT tick__valid" in descriptions[16]
    assert "INPUT enabled" in descriptions[17]

    wires = blueprint["wires"]
    assert isinstance(wires, list)
    assert any(wire[0] == 1 and wire[2] == 1 for wire in wires)
