from __future__ import annotations

from examples.autonomous_mall.autonomous_quality_controller import (
    AutonomousDecisionKind,
    AutonomousQualityController,
)
from examples.autonomous_mall.compiled_quality_policy import (
    PolicyLane,
    compile_quality_policy_book,
)
from examples.autonomous_mall.model import Commodity, Quality
from examples.autonomous_mall.quality_policy_graph import (
    QualityActionKind,
    build_quality_action_graph,
)
from examples.autonomous_mall.recipe_graph import ItemRecipe, RecipeCatalog, build_recipe_dag


def _system(*recipes: ItemRecipe, targets: list[str], raw: set[str]):
    dag = build_recipe_dag(
        RecipeCatalog(list(recipes)),
        targets=targets,
        raw_items=raw,
    )
    graph = build_quality_action_graph(dag)
    book = compile_quality_policy_book(graph)
    return graph, book, AutonomousQualityController(graph, book)


def test_policy_book_compiles_each_root_on_its_own_ancestry() -> None:
    gear = ItemRecipe(
        "gear", "gear", 1, {"iron": 2}, allow_productivity=True, allow_quality=True
    )
    a = ItemRecipe("a", "a", 1, {"gear": 2}, allow_quality=True)
    b = ItemRecipe("b", "b", 1, {"gear": 3}, allow_quality=True)

    _, book, _ = _system(gear, a, b, targets=["a", "b"], raw={"iron"})

    a_target = Commodity("a", Quality.LEGENDARY)
    b_target = Commodity("b", Quality.LEGENDARY)
    assert book.targets == {a_target, b_target}
    assert book.policy_for(a_target).recipe_names == frozenset({"gear", "a"})
    assert book.policy_for(b_target).recipe_names == frozenset({"gear", "b"})

    assert all(
        lane.recipe_name != "b"
        for lane in book.policy_for(a_target).lane_actions
    )
    assert all(
        lane.recipe_name != "a"
        for lane in book.policy_for(b_target).lane_actions
    )


def test_unused_high_quality_lane_gets_compiled_fallback_profile() -> None:
    gear = ItemRecipe(
        "gear", "gear", 1, {"iron": 2}, allow_productivity=True, allow_quality=True
    )
    _, book, _ = _system(gear, targets=["gear"], raw={"iron"})

    target = Commodity("gear", Quality.LEGENDARY)
    epic_lane = PolicyLane(QualityActionKind.CRAFT, "gear", Quality.EPIC)
    assert epic_lane in book.policy_for(target).lane_actions
    assert book.policy_for(target).lane_actions[epic_lane]


def test_live_demand_can_switch_targets_without_campaign_lock() -> None:
    a = ItemRecipe("a", "a", 1, {"iron": 1}, allow_quality=True)
    b = ItemRecipe("b", "b", 1, {"iron": 1}, allow_quality=True)
    _, _, controller = _system(a, b, targets=["a", "b"], raw={"iron"})
    stock = {Commodity("iron", Quality.NORMAL): 100}

    first = controller.decide(
        stock=stock,
        demands={Commodity("a", Quality.LEGENDARY): 1},
    )
    assert first.kind is AutonomousDecisionKind.DISPATCH
    assert first.selected_target == Commodity("a", Quality.LEGENDARY)

    # Before accepting that tentative intent, the desired-stock vector changes.  The
    # controller immediately follows the new demand rather than preserving a campaign.
    second = controller.decide(
        stock=stock,
        demands={Commodity("b", Quality.LEGENDARY): 1},
    )
    assert second.kind is AutonomousDecisionKind.DISPATCH
    assert second.selected_target == Commodity("b", Quality.LEGENDARY)


def test_similarly_empty_targets_are_fair_after_accepted_dispatch() -> None:
    a = ItemRecipe("a", "a", 1, {"iron": 1}, allow_quality=True)
    b = ItemRecipe("b", "b", 1, {"iron": 1}, allow_quality=True)
    _, _, controller = _system(a, b, targets=["a", "b"], raw={"iron"})
    stock = {Commodity("iron", Quality.NORMAL): 100}
    demands = {
        Commodity("a", Quality.LEGENDARY): 1,
        Commodity("b", Quality.LEGENDARY): 1,
    }

    first = controller.decide(stock=stock, demands=demands)
    assert first.intent is not None
    controller.record_dispatch(first.intent)

    second = controller.decide(stock=stock, demands=demands)
    assert second.intent is not None
    assert second.selected_target != first.selected_target


def test_blocked_high_priority_target_does_not_stall_other_work() -> None:
    a = ItemRecipe("a", "a", 1, {"iron": 1}, allow_quality=True)
    # This target is intentionally more expensive, so it wins the tie-breaker before
    # the controller discovers that its steel input is absent.
    b = ItemRecipe("b", "b", 1, {"steel": 10}, allow_quality=True)
    _, _, controller = _system(
        a,
        b,
        targets=["a", "b"],
        raw={"iron", "steel"},
    )

    decision = controller.decide(
        stock={Commodity("iron", Quality.NORMAL): 10},
        demands={
            Commodity("a", Quality.LEGENDARY): 1,
            Commodity("b", Quality.LEGENDARY): 1,
        },
    )

    assert decision.kind is AutonomousDecisionKind.DISPATCH
    assert decision.selected_target == Commodity("a", Quality.LEGENDARY)
    assert decision.intent is not None
    assert decision.intent.action.recipe_name == "a"


def test_existing_epic_raw_stock_enters_epic_lane_without_runtime_lp() -> None:
    gear = ItemRecipe(
        "gear", "gear", 1, {"iron": 2}, allow_productivity=True, allow_quality=True
    )
    _, _, controller = _system(gear, targets=["gear"], raw={"iron"})

    decision = controller.decide(
        stock={Commodity("iron", Quality.EPIC): 2},
        demands={Commodity("gear", Quality.LEGENDARY): 1},
    )

    assert decision.kind is AutonomousDecisionKind.DISPATCH
    assert decision.intent is not None
    assert decision.intent.action.recipe_name == "gear"
    assert decision.intent.action.base_quality is Quality.EPIC


def test_final_reject_is_recycled_before_more_upstream_work() -> None:
    gear = ItemRecipe(
        "gear", "gear", 1, {"iron": 2}, allow_productivity=True, allow_quality=True
    )
    _, _, controller = _system(gear, targets=["gear"], raw={"iron"})

    decision = controller.decide(
        stock={
            Commodity("gear", Quality.RARE): 1,
            Commodity("iron", Quality.NORMAL): 100,
        },
        demands={Commodity("gear", Quality.LEGENDARY): 1},
    )

    assert decision.kind is AutonomousDecisionKind.DISPATCH
    assert decision.intent is not None
    assert decision.intent.action.kind is QualityActionKind.RECYCLE
    assert decision.intent.action.base_quality is Quality.RARE


def test_current_stock_can_satisfy_changed_demand_without_dispatch() -> None:
    a = ItemRecipe("a", "a", 1, {"iron": 1}, allow_quality=True)
    _, _, controller = _system(a, targets=["a"], raw={"iron"})

    decision = controller.decide(
        stock={Commodity("a", Quality.LEGENDARY): 3},
        demands={Commodity("a", Quality.LEGENDARY): 2},
    )

    assert decision.kind is AutonomousDecisionKind.SATISFIED
    assert decision.intent is None


def test_zero_demand_removes_target_immediately() -> None:
    a = ItemRecipe("a", "a", 1, {"iron": 1}, allow_quality=True)
    _, _, controller = _system(a, targets=["a"], raw={"iron"})

    decision = controller.decide(
        stock={Commodity("iron", Quality.NORMAL): 10},
        demands={Commodity("a", Quality.LEGENDARY): 0},
    )

    assert decision.kind is AutonomousDecisionKind.SATISFIED


def test_uncompiled_target_is_rejected_explicitly() -> None:
    a = ItemRecipe("a", "a", 1, {"iron": 1}, allow_quality=True)
    _, _, controller = _system(a, targets=["a"], raw={"iron"})

    try:
        controller.decide(
            stock={Commodity("iron", Quality.NORMAL): 10},
            demands={Commodity("b", Quality.LEGENDARY): 1},
        )
    except ValueError as exc:
        assert "uncompiled mall targets" in str(exc)
    else:
        raise AssertionError("expected uncompiled demand to be rejected")
