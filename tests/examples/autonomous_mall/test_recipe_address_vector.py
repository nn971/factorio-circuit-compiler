from __future__ import annotations

from examples.autonomous_mall.compiled_quality_policy import compile_quality_policy_book
from examples.autonomous_mall.quality_policy_graph import build_quality_action_graph
from examples.autonomous_mall.quality_policy_rom import compile_quality_policy_rom
from examples.autonomous_mall.recipe_graph import ItemRecipe, RecipeCatalog, build_recipe_dag
from examples.autonomous_mall.signal_keyed_policy_rom import (
    build_recipe_address_vector,
    build_signal_keyed_policy_pages,
    descriptor_is_valid,
)


def _graph_and_rom():
    recipes = [
        ItemRecipe("wire-special", "wire", 2, {"copper": 1}, allow_productivity=True),
        ItemRecipe("machine-special", "machine", 1, {"wire": 3}, allow_quality=True),
    ]
    dag = build_recipe_dag(
        RecipeCatalog(recipes),
        targets=["machine"],
        raw_items={"copper"},
        overrides={"wire": "wire-special", "machine": "machine-special"},
    )
    graph = build_quality_action_graph(dag)
    book = compile_quality_policy_book(graph)
    return graph, compile_quality_policy_rom(graph, book)


def test_recipe_address_vector_maps_ids_to_product_item_signals_not_recipe_names() -> None:
    graph, rom = _graph_and_rom()
    addresses = build_recipe_address_vector(graph, rom)

    wire_id = rom.recipe_names.index("wire-special")
    machine_id = rom.recipe_names.index("machine-special")
    assert addresses.item_for_recipe_id(wire_id) == "wire"
    assert addresses.item_for_recipe_id(machine_id) == "machine"
    assert addresses.recipe_id_for_item("wire") == wire_id
    assert addresses.recipe_id_for_item("machine") == machine_id
    assert "wire-special" not in addresses.entries


def test_physical_page_descriptor_has_explicit_validity_bit_even_for_recipe_zero() -> None:
    _, rom = _graph_and_rom()
    pages = build_signal_keyed_policy_pages(rom)
    target_item = "machine"

    descriptor = pages.page(0, "descriptor").entries[target_item]
    assert descriptor_is_valid(descriptor)
    # The first canonical recipe has recipe_id 0; validity therefore cannot be inferred
    # from a non-zero recipe-id field.
    assert pages.lookup_record(next(iter(rom.targets)), 0).recipe_id == 0
