from __future__ import annotations

from examples.autonomous_mall.compiled_quality_policy import compile_quality_policy_book
from examples.autonomous_mall.model import Commodity, Quality
from examples.autonomous_mall.quality_policy_graph import build_quality_action_graph
from examples.autonomous_mall.quality_policy_rom import compile_quality_policy_rom
from examples.autonomous_mall.recipe_graph import ItemRecipe, RecipeCatalog, build_recipe_dag
from examples.autonomous_mall.signal_keyed_policy_rom import (
    build_signal_keyed_policy_pages,
    pairwise_each_lookup,
    reduce_single_signal,
)


def _rom():
    recipes = [
        ItemRecipe(
            "gear",
            "gear",
            1,
            {"iron": 2},
            allow_productivity=True,
            allow_quality=True,
        ),
        ItemRecipe(
            "machine-a",
            "machine-a",
            1,
            {"gear": 1},
            allow_productivity=False,
            allow_quality=True,
        ),
        ItemRecipe(
            "machine-b",
            "machine-b",
            1,
            {"gear": 1},
            allow_productivity=False,
            allow_quality=True,
        ),
    ]
    dag = build_recipe_dag(
        RecipeCatalog(recipes),
        targets=["machine-a", "machine-b"],
        raw_items={"iron"},
    )
    graph = build_quality_action_graph(dag)
    book = compile_quality_policy_book(graph)
    return compile_quality_policy_rom(graph, book)


def test_pages_round_trip_every_target_record() -> None:
    rom = _rom()
    pages = build_signal_keyed_policy_pages(rom)

    for target, policy in rom.targets.items():
        for index, record in enumerate(policy.records):
            assert pages.lookup_record(target, index) == record
        for index in range(len(policy.records), pages.max_records):
            assert pages.lookup_record(target, index) is None


def test_pairwise_each_uses_item_identity_as_associative_key() -> None:
    rom = _rom()
    pages = build_signal_keyed_policy_pages(rom)
    target = Commodity("machine-b", Quality.LEGENDARY)
    page = pages.page(0, "descriptor")

    masked = pairwise_each_lookup(
        selected={target.item: 1},
        rom_vector=page.entries,
    )

    assert set(masked) == {target.item}
    assert reduce_single_signal(masked) == page.entries[target.item]


def test_unselected_target_words_do_not_leak_through_pairwise_lookup() -> None:
    rom = _rom()
    pages = build_signal_keyed_policy_pages(rom)
    page = pages.page(0, "schedules")

    masked = pairwise_each_lookup(
        selected={"machine-a": 1},
        rom_vector=page.entries,
    )

    assert "machine-b" not in masked
    assert masked == {"machine-a": page.entries["machine-a"]}


def test_page_storage_matches_rom_estimator_for_small_target_set() -> None:
    rom = _rom()
    pages = build_signal_keyed_policy_pages(rom)

    # Two targets fit in one 20-slot constant on each descriptor/schedule page.
    assert pages.max_records == 2
    assert pages.constant_combinator_count == 4
