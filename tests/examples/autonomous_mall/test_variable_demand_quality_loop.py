from __future__ import annotations

from examples.autonomous_mall.autonomous_quality_controller import (
    AutonomousDecisionKind,
    AutonomousQualityController,
)
from examples.autonomous_mall.compiled_quality_policy import compile_quality_policy_book
from examples.autonomous_mall.model import Commodity, Quality
from examples.autonomous_mall.quality_controller import FakeQualityDispatcher
from examples.autonomous_mall.quality_policy_graph import build_quality_action_graph
from examples.autonomous_mall.recipe_graph import ItemRecipe, RecipeCatalog, build_recipe_dag


def test_demand_change_during_inflight_work_replans_after_actual_completion() -> None:
    a = ItemRecipe("a", "a", 1, {"iron": 1}, allow_quality=True)
    b = ItemRecipe("b", "b", 1, {"iron": 1}, allow_quality=True)
    dag = build_recipe_dag(
        RecipeCatalog([a, b]),
        targets=["a", "b"],
        raw_items={"iron"},
    )
    graph = build_quality_action_graph(dag)
    book = compile_quality_policy_book(graph)
    controller = AutonomousQualityController(graph, book)
    dispatcher = FakeQualityDispatcher({Commodity("iron", Quality.NORMAL): 100})

    old_demand = {Commodity("a", Quality.LEGENDARY): 1}
    first = controller.decide(
        stock=dispatcher.stock,
        demands=old_demand,
        busy=dispatcher.busy,
    )
    assert first.kind is AutonomousDecisionKind.DISPATCH
    assert first.intent is not None
    assert first.selected_target == Commodity("a", Quality.LEGENDARY)

    dispatcher.dispatch(first.intent)
    controller.record_dispatch(first.intent)

    # While the hard job is in flight the player/controller target changes completely.
    new_demand = {Commodity("b", Quality.LEGENDARY): 1}
    while_busy = controller.decide(
        stock=dispatcher.stock,
        demands=new_demand,
        busy=dispatcher.busy,
    )
    assert while_busy.kind is AutonomousDecisionKind.BUSY

    # The already-committed A craft finishes with an ordinary (nonlegendary) result.
    # That output becomes stock, but it no longer creates an A campaign by itself.
    dispatcher.finish({Commodity("a", Quality.NORMAL): 1})

    replanned = controller.decide(
        stock=dispatcher.stock,
        demands=new_demand,
        busy=dispatcher.busy,
    )
    assert replanned.kind is AutonomousDecisionKind.DISPATCH
    assert replanned.intent is not None
    assert replanned.selected_target == Commodity("b", Quality.LEGENDARY)
    assert replanned.intent.action.recipe_name == "b"
