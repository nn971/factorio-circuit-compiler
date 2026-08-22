from __future__ import annotations

from fractions import Fraction

from examples.autonomous_mall.model import Commodity, Quality
from examples.autonomous_mall.quality_controller import (
    FakeQualityDispatcher,
    QualityDecisionKind,
    RecedingHorizonQualityController,
)
from examples.autonomous_mall.quality_policy_graph import build_quality_action_graph
from examples.autonomous_mall.recipe_graph import ItemRecipe, RecipeCatalog, build_recipe_dag


def _controller(*recipes: ItemRecipe, target: str, raw: set[str], target_quality=Quality.LEGENDARY):
    dag = build_recipe_dag(RecipeCatalog(list(recipes)), targets=[target], raw_items=raw)
    graph = build_quality_action_graph(dag)
    return RecedingHorizonQualityController(
        graph,
        target_item=target,
        target_quality=target_quality,
    )


def test_satisfied_target_dispatches_nothing() -> None:
    recipe = ItemRecipe("gear", "gear", 1, {"iron": 2}, allow_productivity=True)
    controller = _controller(recipe, target="gear", raw={"iron"})

    decision = controller.decide({Commodity("gear", Quality.LEGENDARY): 1})

    assert decision.kind is QualityDecisionKind.SATISFIED
    assert decision.intent is None


def test_busy_controller_waits_for_actual_completion_before_replanning() -> None:
    recipe = ItemRecipe("gear", "gear", 1, {"iron": 2}, allow_productivity=True)
    controller = _controller(recipe, target="gear", raw={"iron"})

    decision = controller.decide({Commodity("iron", Quality.NORMAL): 100}, busy=True)

    assert decision.kind is QualityDecisionKind.BUSY
    assert decision.intent is None


def test_controller_never_conjures_economic_raw_imports() -> None:
    recipe = ItemRecipe("gear", "gear", 1, {"iron": 2}, allow_productivity=True)
    controller = _controller(recipe, target="gear", raw={"iron"})

    decision = controller.decide({})

    assert decision.kind is QualityDecisionKind.BLOCKED
    assert decision.intent is None
    assert decision.plan is not None
    assert decision.plan.raw_required[Commodity("iron", Quality.NORMAL)] > 0
    assert decision.blocked_on[Commodity("iron", Quality.NORMAL)] == 2


def test_existing_high_quality_raw_stock_enters_that_quality_lane() -> None:
    recipe = ItemRecipe(
        "gear",
        "gear",
        1,
        {"iron": 2},
        allow_productivity=True,
        allow_quality=True,
    )
    controller = _controller(recipe, target="gear", raw={"iron"})

    decision = controller.decide({Commodity("iron", Quality.EPIC): 2})

    assert decision.kind is QualityDecisionKind.DISPATCH
    assert decision.intent is not None
    action = decision.intent.action
    assert action.recipe_name == "gear"
    assert action.base_quality is Quality.EPIC
    assert action.inputs == {Commodity("iron", Quality.EPIC): 2}


def test_final_reject_is_recycled_before_more_crafting() -> None:
    recipe = ItemRecipe(
        "gear",
        "gear",
        1,
        {"iron": 2},
        allow_productivity=True,
        allow_quality=True,
    )
    controller = _controller(recipe, target="gear", raw={"iron"})

    decision = controller.decide(
        {
            Commodity("gear", Quality.RARE): 1,
            Commodity("iron", Quality.NORMAL): 100,
        }
    )

    assert decision.kind is QualityDecisionKind.DISPATCH
    assert decision.intent is not None
    assert decision.intent.action.kind.value == "recycle"
    assert decision.intent.action.base_quality is Quality.RARE


def test_furthest_downstream_feasible_recipe_is_dispatched_first() -> None:
    gear = ItemRecipe(
        "gear",
        "gear",
        1,
        {"iron": 2},
        allow_productivity=True,
        allow_quality=True,
    )
    machine = ItemRecipe(
        "machine",
        "machine",
        1,
        {"gear": 3},
        allow_productivity=False,
        allow_quality=True,
    )
    controller = _controller(
        gear,
        machine,
        target="machine",
        raw={"iron"},
        target_quality=Quality.NORMAL,
    )

    decision = controller.decide(
        {
            Commodity("gear", Quality.NORMAL): 3,
            Commodity("iron", Quality.NORMAL): 100,
        }
    )

    assert decision.kind is QualityDecisionKind.DISPATCH
    assert decision.intent is not None
    assert decision.intent.action.recipe_name == "machine"
    assert decision.intent.action.base_quality is Quality.NORMAL


def test_fake_dispatcher_reserves_inputs_and_applies_actual_outputs() -> None:
    recipe = ItemRecipe(
        "gear",
        "gear",
        1,
        {"iron": 2},
        allow_productivity=True,
        allow_quality=True,
    )
    controller = _controller(recipe, target="gear", raw={"iron"})
    dispatcher = FakeQualityDispatcher({Commodity("iron", Quality.EPIC): 2})

    decision = controller.decide(dispatcher.stock, busy=dispatcher.busy)
    assert decision.intent is not None
    dispatcher.dispatch(decision.intent)
    assert dispatcher.busy
    assert Commodity("iron", Quality.EPIC) not in dispatcher.stock

    # Feed an intentionally lucky real outcome.  The controller sees the actual stock
    # on the next decision rather than relying on the action's expected output vector.
    dispatcher.finish({Commodity("gear", Quality.LEGENDARY): 1})
    assert not dispatcher.busy
    assert dispatcher.stock[Commodity("gear", Quality.LEGENDARY)] == 1

    follow_up = controller.decide(dispatcher.stock, busy=dispatcher.busy)
    assert follow_up.kind is QualityDecisionKind.SATISFIED


def test_fake_dispatcher_cancel_restores_reserved_inputs() -> None:
    recipe = ItemRecipe("gear", "gear", 1, {"iron": 2}, allow_productivity=True)
    controller = _controller(recipe, target="gear", raw={"iron"}, target_quality=Quality.NORMAL)
    dispatcher = FakeQualityDispatcher({Commodity("iron", Quality.NORMAL): 2})

    decision = controller.decide(dispatcher.stock)
    assert decision.intent is not None
    dispatcher.dispatch(decision.intent)
    dispatcher.cancel()

    assert dispatcher.stock == {Commodity("iron", Quality.NORMAL): Fraction(2)}
