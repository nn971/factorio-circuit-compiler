from __future__ import annotations

from fractions import Fraction

from examples.autonomous_mall.model import Commodity, Quality
from examples.autonomous_mall.quality_policy import solve_quality_policy
from examples.autonomous_mall.quality_policy_graph import (
    QualityActionKind,
    QualityPolicyConfig,
    build_quality_action_graph,
)
from examples.autonomous_mall.recipe_graph import ItemRecipe, RecipeCatalog, build_recipe_dag


def _graph(
    *recipes: ItemRecipe,
    target: str,
    raw: set[str],
    config: QualityPolicyConfig | None = None,
):
    dag = build_recipe_dag(RecipeCatalog(list(recipes)), targets=[target], raw_items=raw)
    return build_quality_action_graph(dag, config=config)


def test_normal_target_uses_material_best_productivity_profile() -> None:
    recipe = ItemRecipe(
        "gear",
        "gear",
        1,
        {"iron": 2},
        allow_productivity=True,
        allow_quality=True,
    )
    graph = _graph(recipe, target="gear", raw={"iron"})

    plan = solve_quality_policy(
        graph,
        targets={Commodity("gear", Quality.NORMAL): 1},
    )

    assert plan.raw_required == {Commodity("iron", Quality.NORMAL): 1}
    assert len(plan.steps) == 1
    assert plan.steps[0].action.module_profile.name == "4p0q"
    assert plan.steps[0].expected_runs == Fraction(1, 2)


def test_existing_target_stock_closes_plan_without_work() -> None:
    recipe = ItemRecipe(
        "gear",
        "gear",
        1,
        {"iron": 2},
        allow_productivity=True,
        allow_quality=True,
    )
    graph = _graph(recipe, target="gear", raw={"iron"})
    target = Commodity("gear", Quality.LEGENDARY)

    plan = solve_quality_policy(graph, targets={target: 1}, stock={target: 1})

    assert plan.raw_total == 0
    assert plan.raw_required == {}
    assert plan.steps == ()


def test_existing_high_quality_intermediate_is_a_free_entry_lane() -> None:
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
        {"gear": 5},
        allow_productivity=False,
        allow_quality=True,
    )
    config = QualityPolicyConfig(quality_chance_per_module=0, recycler_quality_chance_per_module=0)
    graph = _graph(gear, machine, target="machine", raw={"iron"}, config=config)

    plan = solve_quality_policy(
        graph,
        targets={Commodity("machine", Quality.RARE): 1},
        stock={Commodity("gear", Quality.RARE): 5},
    )

    assert plan.raw_total == 0
    assert len(plan.steps) == 1
    assert plan.steps[0].action.recipe_name == "machine"
    assert plan.steps[0].action.base_quality is Quality.RARE
    assert plan.steps[0].expected_runs == 1


def test_legendary_policy_uses_final_recycling_when_it_saves_raw() -> None:
    recipe = ItemRecipe(
        "machine",
        "machine",
        1,
        {"iron": 1},
        allow_productivity=False,
        allow_quality=True,
    )
    config = QualityPolicyConfig(
        module_slots=1,
        productivity_bonus_per_module=0,
        quality_chance_per_module=Fraction(1, 2),
        recycler_module_slots=1,
        recycler_quality_chance_per_module=Fraction(1, 2),
    )
    graph = _graph(recipe, target="machine", raw={"iron"}, config=config)

    plan = solve_quality_policy(
        graph,
        targets={Commodity("machine", Quality.LEGENDARY): 1},
    )

    assert plan.raw_total > 0
    assert any(step.action.kind is QualityActionKind.RECYCLE for step in plan.steps)
    assert all(commodity.quality is Quality.NORMAL for commodity in plan.raw_required)
