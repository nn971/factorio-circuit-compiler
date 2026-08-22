from __future__ import annotations

from examples.autonomous_mall.compiled_quality_policy import compile_quality_policy_book
from examples.autonomous_mall.model import Quality
from examples.autonomous_mall.quality_policy_graph import build_quality_action_graph
from examples.autonomous_mall.quality_policy_rom import (
    RomRecipeRecord,
    compile_quality_policy_rom,
    estimate_signal_keyed_storage,
)
from examples.autonomous_mall.recipe_graph import ItemRecipe, RecipeCatalog, build_recipe_dag


def _two_target_rom():
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
    return graph, book, compile_quality_policy_rom(graph, book)


def test_recipe_record_round_trips_signed_factorio_words() -> None:
    record = RomRecipeRecord(
        recipe_id=65535,
        schedule_ids=(63, 62, 61, 60, 59),
        recycle_final=True,
    )

    descriptor, schedules = record.pack()
    # The packed schedule word deliberately exercises Factorio's signed int32 range.
    assert -2**31 <= descriptor < 2**31
    assert -2**31 <= schedules < 2**31
    assert RomRecipeRecord.unpack(descriptor, schedules) == record


def test_rom_deduplicates_shared_recipe_schedules() -> None:
    graph, book, rom = _two_target_rom()

    a = rom.target_policy(next(target for target in rom.targets if target.item == "machine-a"))
    b = rom.target_policy(next(target for target in rom.targets if target.item == "machine-b"))

    gear_id = rom.recipe_names.index("gear")
    gear_a = next(record for record in a.records if record.recipe_id == gear_id)
    gear_b = next(record for record in b.records if record.recipe_id == gear_id)
    assert gear_a.schedule_ids == gear_b.schedule_ids

    # The rich policy has many recipe/quality lanes, while the ROM stores one compact
    # record per recipe and interns repeated physical schedules globally.
    assert len(a.records) == 2
    assert len(b.records) == 2
    assert len(rom.schedules) < 2 * len(Quality) * 2


def test_only_final_record_has_recycle_flag() -> None:
    _, _, rom = _two_target_rom()

    for target, policy in rom.targets.items():
        flagged = [record for record in policy.records if record.recycle_final]
        assert len(flagged) == 1
        assert rom.recipe_name(flagged[0].recipe_id) == target.item


def test_signal_keyed_storage_estimate_counts_two_words_per_record_page() -> None:
    _, _, rom = _two_target_rom()

    estimate = estimate_signal_keyed_storage(rom)
    assert estimate.target_count == 2
    assert estimate.max_records_per_target == 2
    assert estimate.packed_words == 8
    # Four pages (descriptor/schedule for two record slots), each fitting both targets
    # into a single 20-slot constant combinator.
    assert estimate.constant_combinators_at_20_slots == 4
