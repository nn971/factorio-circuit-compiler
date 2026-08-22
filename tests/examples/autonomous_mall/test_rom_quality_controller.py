from __future__ import annotations

from fractions import Fraction

from examples.autonomous_mall.autonomous_quality_controller import AutonomousQualityController
from examples.autonomous_mall.compiled_quality_policy import compile_quality_policy_book
from examples.autonomous_mall.model import Commodity, Quality
from examples.autonomous_mall.quality_policy_graph import build_quality_action_graph
from examples.autonomous_mall.quality_policy_rom import compile_quality_policy_rom
from examples.autonomous_mall.recipe_graph import ItemRecipe, RecipeCatalog, build_recipe_dag
from examples.autonomous_mall.rom_quality_controller import (
    RomAutonomousQualityController,
    RomDecisionKind,
)


def _controllers():
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
    rom = compile_quality_policy_rom(graph, book)
    return (
        AutonomousQualityController(graph, book),
        RomAutonomousQualityController(graph, rom),
    )


def _assert_same_dispatch(rich, compact, *, stock, demands) -> None:
    rich_decision = rich.decide(stock=stock, demands=demands)
    rom_decision = compact.decide(stock=stock, demands=demands)

    assert rom_decision.kind.value == rich_decision.kind.value
    assert rom_decision.selected_target == rich_decision.selected_target
    if rich_decision.intent is None:
        assert rom_decision.intent is None
        return
    assert rom_decision.intent is not None
    assert rom_decision.intent.action.name == rich_decision.intent.action.name


def test_rom_controller_matches_rich_controller_for_normal_entry_stock() -> None:
    rich, compact = _controllers()
    target = Commodity("machine-a", Quality.LEGENDARY)

    _assert_same_dispatch(
        rich,
        compact,
        stock={Commodity("iron", Quality.NORMAL): 100},
        demands={target: 1},
    )


def test_rom_controller_matches_rich_controller_for_high_quality_shortcut() -> None:
    rich, compact = _controllers()
    target = Commodity("machine-a", Quality.LEGENDARY)

    _assert_same_dispatch(
        rich,
        compact,
        stock={Commodity("gear", Quality.EPIC): 1},
        demands={target: 1},
    )


def test_rom_controller_recycles_final_reject_before_more_crafting() -> None:
    rich, compact = _controllers()
    target = Commodity("machine-a", Quality.LEGENDARY)
    stock = {
        Commodity("machine-a", Quality.RARE): 1,
        Commodity("iron", Quality.NORMAL): 100,
    }

    _assert_same_dispatch(rich, compact, stock=stock, demands={target: 1})
    decision = compact.decide(stock=stock, demands={target: 1})
    assert decision.kind is RomDecisionKind.DISPATCH
    assert decision.intent is not None
    assert decision.intent.action.kind.value == "recycle"
    assert decision.intent.action.base_quality is Quality.RARE


def test_dynamic_demand_switches_target_without_recompiling_rom() -> None:
    rich, compact = _controllers()
    a = Commodity("machine-a", Quality.LEGENDARY)
    b = Commodity("machine-b", Quality.LEGENDARY)
    stock = {Commodity("iron", Quality.NORMAL): 100}

    first_rich = rich.decide(stock=stock, demands={a: 1})
    first_rom = compact.decide(stock=stock, demands={a: 1})
    assert first_rich.intent is not None
    assert first_rom.intent is not None
    assert first_rom.selected_target == a

    rich.record_dispatch(first_rich.intent)
    compact.record_dispatch(first_rom.intent)

    # The old target disappears and a new target appears.  No policy compilation or
    # campaign reset occurs; the next decision follows the new live demand vector.
    _assert_same_dispatch(rich, compact, stock=stock, demands={b: 1})
    switched = compact.decide(stock=stock, demands={b: 1})
    assert switched.selected_target == b


def test_equal_shortages_round_robin_after_accepted_dispatch() -> None:
    rich, compact = _controllers()
    a = Commodity("machine-a", Quality.LEGENDARY)
    b = Commodity("machine-b", Quality.LEGENDARY)
    stock = {Commodity("iron", Quality.NORMAL): 100}
    demands = {a: 1, b: 1}

    first_rich = rich.decide(stock=stock, demands=demands)
    first_rom = compact.decide(stock=stock, demands=demands)
    assert first_rich.intent is not None
    assert first_rom.intent is not None
    assert first_rom.selected_target == first_rich.selected_target

    rich.record_dispatch(first_rich.intent)
    compact.record_dispatch(first_rom.intent)

    second_rich = rich.decide(stock=stock, demands=demands)
    second_rom = compact.decide(stock=stock, demands=demands)
    assert second_rom.selected_target == second_rich.selected_target
    assert second_rom.selected_target != first_rom.selected_target


def test_rom_controller_reports_normal_raw_shortage_without_importing_it() -> None:
    _, compact = _controllers()
    target = Commodity("machine-a", Quality.LEGENDARY)

    decision = compact.decide(stock={}, demands={target: Fraction(1)})

    assert decision.kind is RomDecisionKind.BLOCKED
    assert decision.blocked_on[Commodity("iron", Quality.NORMAL)] == 2
