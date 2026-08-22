from __future__ import annotations

from factorio_circuit.blueprint.layout_encode import layout_to_blueprint_json
from factorio_circuit.synthesis import synthesize_layout

from examples.autonomous_mall.rom_record_mux_probe import build_rom_record_mux_probe


def _entity(entities, marker: str):
    return next(
        entity
        for entity in entities
        if marker in entity.get("player_description", "")
    )


def test_record_mux_probe_serializes_numeric_pointer_decoder_and_pairwise_mux() -> None:
    circuit = build_rom_record_mux_probe(
        selected_item="assembling-machine-2",
        records=[111, -222, 333],
        pointer_index=1,
    )
    layout = synthesize_layout(circuit)
    wrapper = layout_to_blueprint_json(layout)
    entities = wrapper["blueprint"]["entities"]

    pointer = _entity(entities, "POINTER decode")
    pointer_conditions = pointer["control_behavior"]["decider_conditions"]
    condition = pointer_conditions["conditions"][0]
    output = pointer_conditions["outputs"][0]
    assert condition["first_signal"]["name"] == "signal-each"
    assert condition["second_signal"]["name"] == "signal-P"
    assert condition["comparator"] == "="
    assert output["signal"]["name"] == "signal-each"
    assert output["copy_count_from_input"] is False
    assert condition["first_signal_networks"] != condition["second_signal_networks"]

    mux = _entity(entities, "RECORD MUX")
    arithmetic = mux["control_behavior"]["arithmetic_conditions"]
    assert arithmetic["operation"] == "*"
    assert arithmetic["first_signal"]["name"] == "signal-each"
    assert arithmetic["second_signal"]["name"] == "signal-each"
    assert arithmetic["output_signal"]["name"] == "signal-each"
    assert arithmetic["first_signal_networks"] != arithmetic["second_signal_networks"]

    reducer = _entity(entities, "SELECTED RECORD -> signal-I")
    reducer_conditions = reducer["control_behavior"]["decider_conditions"]
    reducer_output = reducer_conditions["outputs"][0]
    assert reducer_output["signal"]["name"] == "signal-I"
    assert reducer_output["copy_count_from_input"] is True


def test_record_mux_probe_has_one_target_lookup_per_record_slot() -> None:
    circuit = build_rom_record_mux_probe(
        selected_item="assembling-machine-2",
        records=[10, 20, 30, 40],
        pointer_index=3,
    )
    descriptions = [getattr(entity, "description", "") or "" for entity in circuit.entities]
    assert sum(description.startswith("TARGET LOOKUP record") for description in descriptions) == 4
    assert sum(description.startswith("RECORD ") and " -> signal-" in description for description in descriptions) == 4
