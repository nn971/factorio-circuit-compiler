from __future__ import annotations

from fractions import Fraction

from examples.autonomous_mall.model import Commodity, Quality
from examples.autonomous_mall.quality_policy_graph import (
    QualityActionKind,
    QualityPolicyConfig,
    build_quality_action_graph,
)
from examples.autonomous_mall.recipe_graph import ItemRecipe, RecipeCatalog, build_recipe_dag


def _dag(*recipes: ItemRecipe, target: str, raw: set[str]):
    return build_recipe_dag(RecipeCatalog(list(recipes)), targets=[target], raw_items=raw)


def test_expands_every_required_item_into_five_quality_states() -> None:
    dag = _dag(ItemRecipe("gear", "gear", 1, {"iron": 2}), target="gear", raw={"iron"})
    graph = build_quality_action_graph(dag)

    assert len(graph.commodities) == 10
    assert Commodity("iron", Quality.NORMAL) in graph.commodities
    assert Commodity("iron", Quality.LEGENDARY) in graph.commodities
    assert Commodity("gear", Quality.NORMAL) in graph.commodities
    assert Commodity("gear", Quality.LEGENDARY) in graph.commodities


def test_mixed_profile_combines_productivity_and_quality_exactly() -> None:
    recipe = ItemRecipe(
        "gear",
        "gear",
        1,
        {"iron": 2},
        allow_productivity=True,
        allow_quality=True,
    )
    graph = build_quality_action_graph(_dag(recipe, target="gear", raw={"iron"}))

    actions = [
        action
        for action in graph.actions_for("gear", Quality.NORMAL)
        if action.kind is QualityActionKind.CRAFT and action.module_profile.name == "2p2q"
    ]
    assert len(actions) == 1
    action = actions[0]
    assert action.module_profile.productivity_bonus == Fraction(1, 2)
    assert action.module_profile.quality_chance == Fraction(1, 8)
    assert action.inputs == {Commodity("iron", Quality.NORMAL): 2}
    assert sum(action.outputs.values(), start=Fraction(0)) == Fraction(3, 2)
    assert action.outputs[Commodity("gear", Quality.NORMAL)] == Fraction(21, 16)
    assert action.outputs[Commodity("gear", Quality.UNCOMMON)] == Fraction(27, 160)


def test_legendary_lane_uses_all_productivity_when_legal() -> None:
    recipe = ItemRecipe(
        "gear",
        "gear",
        1,
        {"iron": 2},
        allow_productivity=True,
        allow_quality=True,
    )
    graph = build_quality_action_graph(_dag(recipe, target="gear", raw={"iron"}))

    legendary = [
        action
        for action in graph.actions_for("gear", Quality.LEGENDARY)
        if action.kind is QualityActionKind.CRAFT
    ]
    assert len(legendary) == 1
    assert legendary[0].module_profile.name == "4p0q"
    assert legendary[0].outputs == {Commodity("gear", Quality.LEGENDARY): 2}


def test_recipe_without_productivity_has_one_quality_profile_per_lane() -> None:
    recipe = ItemRecipe(
        "machine",
        "machine",
        1,
        {"gear": 5},
        allow_productivity=False,
        allow_quality=True,
    )
    graph = build_quality_action_graph(_dag(recipe, target="machine", raw={"gear"}))

    for quality in Quality:
        crafts = [
            action
            for action in graph.actions_for("machine", quality)
            if action.kind is QualityActionKind.CRAFT
        ]
        assert len(crafts) == 1
        assert crafts[0].module_profile.name == "0p4q"


def test_only_nonlegendary_final_product_is_recycled() -> None:
    intermediate = ItemRecipe(
        "gear",
        "gear",
        1,
        {"iron": 2},
        allow_productivity=True,
        allow_quality=True,
    )
    final = ItemRecipe(
        "machine",
        "machine",
        1,
        {"gear": 5},
        allow_productivity=False,
        allow_quality=True,
    )
    graph = build_quality_action_graph(
        _dag(intermediate, final, target="machine", raw={"iron"})
    )

    recycler_actions = [action for action in graph.actions if action.kind is QualityActionKind.RECYCLE]
    assert len(recycler_actions) == 4
    assert {action.recipe_name for action in recycler_actions} == {"machine"}
    assert {action.base_quality for action in recycler_actions} == {
        Quality.NORMAL,
        Quality.UNCOMMON,
        Quality.RARE,
        Quality.EPIC,
    }
    assert all(action.module_profile.name == "0p4q" for action in recycler_actions)


def test_productivity_cap_can_make_lower_quality_profiles_materially_dominated() -> None:
    recipe = ItemRecipe(
        "gear",
        "gear",
        1,
        {"iron": 2},
        allow_productivity=True,
        allow_quality=True,
        maximum_productivity=Fraction(1, 2),
    )
    config = QualityPolicyConfig(
        module_slots=4,
        productivity_bonus_per_module=Fraction(1, 4),
        quality_chance_per_module=Fraction(1, 16),
    )
    graph = build_quality_action_graph(_dag(recipe, target="gear", raw={"iron"}), config=config)

    names = {
        action.module_profile.name
        for action in graph.actions_for("gear", Quality.NORMAL)
        if action.kind is QualityActionKind.CRAFT
    }
    assert names == {"0p4q", "1p3q", "2p2q"}


def test_vanilla_assembling_machine_2_shape_has_77_actions() -> None:
    recipes = [
        ItemRecipe(
            "copper-cable",
            "copper-cable",
            2,
            {"copper-plate": 1},
            allow_productivity=True,
            allow_quality=True,
        ),
        ItemRecipe(
            "electronic-circuit",
            "electronic-circuit",
            1,
            {"copper-cable": 3, "iron-plate": 1},
            allow_productivity=True,
            allow_quality=True,
        ),
        ItemRecipe(
            "iron-gear-wheel",
            "iron-gear-wheel",
            1,
            {"iron-plate": 2},
            allow_productivity=True,
            allow_quality=True,
        ),
        ItemRecipe(
            "assembling-machine-1",
            "assembling-machine-1",
            1,
            {"electronic-circuit": 3, "iron-gear-wheel": 5, "iron-plate": 9},
            allow_productivity=False,
            allow_quality=True,
        ),
        ItemRecipe(
            "assembling-machine-2",
            "assembling-machine-2",
            1,
            {"assembling-machine-1": 1, "electronic-circuit": 3, "iron-gear-wheel": 5, "steel-plate": 2},
            allow_productivity=False,
            allow_quality=True,
        ),
    ]
    dag = _dag(
        *recipes,
        target="assembling-machine-2",
        raw={"copper-plate", "iron-plate", "steel-plate"},
    )
    graph = build_quality_action_graph(dag)

    # 3 productivity-capable recipes: 4 nonlegendary lanes * 5 P/Q splits +
    # one all-productivity legendary lane = 21 each.  Two quality-only recipes have
    # five lanes each.  Final-only recycling contributes four more actions.
    assert len(graph.actions) == 3 * 21 + 2 * 5 + 4
