from __future__ import annotations

from factorio_circuit.blueprint.layout_encode import layout_to_blueprint_json
from factorio_circuit.synthesis import synthesize_layout

from examples.autonomous_mall.rom_lookup_probe import build_rom_lookup_probe


def test_probe_serializes_pairwise_each_with_opposite_input_networks() -> None:
    circuit = build_rom_lookup_probe(
        selected_item="assembling-machine-1",
        entries={
            "assembling-machine-1": 12345,
            "assembling-machine-2": -67890,
        },
    )
    layout = synthesize_layout(circuit)
    wrapper = layout_to_blueprint_json(layout)
    entities = wrapper["blueprint"]["entities"]

    pairwise = next(
        entity
        for entity in entities
        if "ROM lookup: Each(net A) * Each(net B) -> Each"
        in entity.get("player_description", "")
    )
    conditions = pairwise["control_behavior"]["arithmetic_conditions"]
    assert conditions["operation"] == "*"
    assert conditions["first_signal"]["name"] == "signal-each"
    assert conditions["second_signal"]["name"] == "signal-each"
    assert conditions["output_signal"]["name"] == "signal-each"

    first = conditions["first_signal_networks"]
    second = conditions["second_signal_networks"]
    assert first != second
    assert {tuple(sorted(first.items())), tuple(sorted(second.items()))} == {
        (("green", False), ("red", True)),
        (("green", True), ("red", False)),
    }

    reducer = next(
        entity
        for entity in entities
        if "Reduce selected packed word to signal-I"
        in entity.get("player_description", "")
    )
    decider = reducer["control_behavior"]["decider_conditions"]
    condition = decider["conditions"][0]
    output = decider["outputs"][0]
    assert condition["first_signal"]["name"] == "signal-each"
    assert condition["comparator"] == "≠"
    assert output["signal"]["name"] == "signal-I"
    assert output["copy_count_from_input"] is True


def test_probe_rom_constants_split_after_twenty_item_signals() -> None:
    entries = {f"item-{index}": index + 1 for index in range(21)}
    circuit = build_rom_lookup_probe(selected_item="item-0", entries=entries)

    rom_chunks = [
        entity
        for entity in circuit.entities
        if getattr(entity, "description", "").startswith("ROM PAGE chunk")
    ]
    assert len(rom_chunks) == 2
    assert sorted(len(entity.signals) for entity in rom_chunks) == [1, 20]
