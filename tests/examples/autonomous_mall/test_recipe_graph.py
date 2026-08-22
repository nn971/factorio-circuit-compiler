from __future__ import annotations

from fractions import Fraction

import pytest

from examples.autonomous_mall.factorio_data import catalog_from_data_raw
from examples.autonomous_mall.recipe_graph import (
    AmbiguousProducerError,
    InvalidRecipeOverrideError,
    ItemRecipe,
    MissingProducerError,
    RecipeCatalog,
    RecipeCycleError,
    build_recipe_dag,
)


def test_extracts_supported_factorio_recipe_metadata() -> None:
    catalog, report = catalog_from_data_raw(
        {
            "recipe": {
                "gear": {
                    "ingredients": [{"type": "item", "name": "iron", "amount": 2}],
                    "results": [{"type": "item", "name": "gear", "amount": 1}],
                    "energy_required": 0.5,
                    "allow_productivity": True,
                    "allow_quality": True,
                    "maximum_productivity": 1.25,
                    "category": "crafting",
                },
                "fluid-thing": {
                    "ingredients": [{"type": "fluid", "name": "water", "amount": 10}],
                    "results": [{"type": "item", "name": "thing", "amount": 1}],
                },
                "byproduct": {
                    "ingredients": [{"type": "item", "name": "ore", "amount": 1}],
                    "results": [
                        {"type": "item", "name": "a", "amount": 1},
                        {"type": "item", "name": "b", "amount": 1},
                    ],
                },
                "random": {
                    "ingredients": [{"type": "item", "name": "ore", "amount": 1}],
                    "results": [
                        {"type": "item", "name": "random", "amount": 1, "probability": 0.5}
                    ],
                },
            }
        }
    )

    gear = catalog.recipe_named("gear")
    assert gear.product == "gear"
    assert gear.product_amount == 1
    assert gear.ingredients == {"iron": Fraction(2)}
    assert gear.energy_required == Fraction(1, 2)
    assert gear.allow_productivity is True
    assert gear.allow_quality is True
    assert gear.maximum_productivity == Fraction(5, 4)
    assert report.total_prototypes == 4
    assert report.accepted == 1
    assert report.ignored_by_reason == {
        "fluid-ingredient": 1,
        "multiple-products": 1,
        "probabilistic-product": 1,
    }


def test_builds_topological_dag_to_explicit_raw_boundary() -> None:
    catalog = RecipeCatalog(
        [
            ItemRecipe("gear", "gear", 1, {"iron": 2}),
            ItemRecipe("engine", "engine", 1, {"gear": 1, "iron": 1}),
            ItemRecipe("machine", "machine", 1, {"engine": 2, "gear": 3}),
        ]
    )

    dag = build_recipe_dag(catalog, targets=["machine"], raw_items={"iron"})

    assert [recipe.name for recipe in dag.recipes] == ["gear", "engine", "machine"]
    assert dag.recipe_for("engine") is catalog.recipe_named("engine")
    assert dag.recipe_for("iron") is None
    assert dag.raw_items == frozenset({"iron"})
    assert dag.required_items == frozenset({"iron", "gear", "engine", "machine"})


def test_shared_ancestry_is_emitted_once() -> None:
    catalog = RecipeCatalog(
        [
            ItemRecipe("gear", "gear", 1, {"iron": 2}),
            ItemRecipe("a", "a", 1, {"gear": 1}),
            ItemRecipe("b", "b", 1, {"gear": 2}),
        ]
    )

    dag = build_recipe_dag(catalog, targets=["a", "b"], raw_items={"iron"})

    assert [recipe.name for recipe in dag.recipes] == ["gear", "a", "b"]


def test_same_name_recipe_is_canonical_when_multiple_producers_exist() -> None:
    catalog = RecipeCatalog(
        [
            ItemRecipe("gear", "gear", 1, {"iron": 2}),
            ItemRecipe("gear-from-scrap", "gear", 1, {"scrap": 3}),
        ]
    )

    dag = build_recipe_dag(
        catalog,
        targets=["gear"],
        raw_items={"iron", "scrap"},
    )

    assert [recipe.name for recipe in dag.recipes] == ["gear"]


def test_override_selects_noncanonical_recipe() -> None:
    catalog = RecipeCatalog(
        [
            ItemRecipe("gear", "gear", 1, {"iron": 2}),
            ItemRecipe("gear-from-scrap", "gear", 1, {"scrap": 3}),
        ]
    )

    dag = build_recipe_dag(
        catalog,
        targets=["gear"],
        raw_items={"iron", "scrap"},
        overrides={"gear": "gear-from-scrap"},
    )

    assert [recipe.name for recipe in dag.recipes] == ["gear-from-scrap"]


def test_ambiguous_producer_requires_override() -> None:
    catalog = RecipeCatalog(
        [
            ItemRecipe("gear-a", "gear", 1, {"iron": 2}),
            ItemRecipe("gear-b", "gear", 1, {"scrap": 3}),
        ]
    )

    with pytest.raises(AmbiguousProducerError, match="configure an override"):
        build_recipe_dag(catalog, targets=["gear"], raw_items={"iron", "scrap"})


def test_invalid_override_is_rejected() -> None:
    catalog = RecipeCatalog(
        [
            ItemRecipe("gear", "gear", 1, {"iron": 2}),
            ItemRecipe("circuit", "circuit", 1, {"copper": 3}),
        ]
    )

    with pytest.raises(InvalidRecipeOverrideError, match="produces 'circuit'"):
        build_recipe_dag(
            catalog,
            targets=["gear"],
            raw_items={"iron", "copper"},
            overrides={"gear": "circuit"},
        )


def test_unconfigured_leaf_is_not_silently_treated_as_raw() -> None:
    catalog = RecipeCatalog([ItemRecipe("gear", "gear", 1, {"iron": 2})])

    with pytest.raises(MissingProducerError, match="'iron'"):
        build_recipe_dag(catalog, targets=["gear"], raw_items=set())


def test_selected_recipe_cycle_is_reported() -> None:
    catalog = RecipeCatalog(
        [
            ItemRecipe("a", "a", 1, {"b": 1}),
            ItemRecipe("b", "b", 1, {"a": 1}),
        ]
    )

    with pytest.raises(RecipeCycleError, match="a -> b -> a"):
        build_recipe_dag(catalog, targets=["a"], raw_items=set())
