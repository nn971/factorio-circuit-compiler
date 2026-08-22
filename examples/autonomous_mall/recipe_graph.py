"""Canonical item-recipe DAGs for the autonomous-mall quality planner.

This module intentionally models only the simple recipe backbone needed by the first
quality-policy prototype: item-only ingredients and one deterministic item product.
Quality, productivity and recycling are layered on top of this backbone later.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from types import MappingProxyType
from typing import Mapping, Sequence

Amount = Fraction


class RecipeGraphError(RuntimeError):
    """Base class for canonical recipe-graph construction failures."""


class MissingProducerError(RecipeGraphError):
    """An item outside the configured raw boundary has no supported producer."""


class AmbiguousProducerError(RecipeGraphError):
    """An item has multiple supported producers and no canonical choice."""


class RecipeCycleError(RecipeGraphError):
    """The selected canonical recipes contain a dependency cycle."""


class InvalidRecipeOverrideError(RecipeGraphError):
    """A recipe override does not produce the item it was assigned to."""


@dataclass(frozen=True)
class ItemRecipe:
    """One deterministic, single-item-output recipe prototype.

    ``ingredients`` and ``product_amount`` are exact rational amounts. Metadata used
    later by the quality-policy compiler is retained here so game-data extraction is a
    one-time operation rather than a second prototype-data pass.
    """

    name: str
    product: str
    product_amount: Amount
    ingredients: Mapping[str, Amount]
    category: str = "crafting"
    energy_required: Amount = Fraction(1, 2)
    allow_productivity: bool = False
    allow_quality: bool = True
    maximum_productivity: Amount = Fraction(3)
    main_product: str | None = None

    def __post_init__(self) -> None:
        product_amount = Fraction(self.product_amount)
        if product_amount <= 0:
            raise ValueError("product_amount must be positive")
        ingredients = {
            item: Fraction(amount)
            for item, amount in self.ingredients.items()
            if Fraction(amount)
        }
        if any(amount < 0 for amount in ingredients.values()):
            raise ValueError("ingredient amounts must be non-negative")
        if Fraction(self.energy_required) <= 0:
            raise ValueError("energy_required must be positive")
        if Fraction(self.maximum_productivity) < 0:
            raise ValueError("maximum_productivity must be non-negative")
        object.__setattr__(self, "product_amount", product_amount)
        object.__setattr__(self, "ingredients", MappingProxyType(ingredients))
        object.__setattr__(self, "energy_required", Fraction(self.energy_required))
        object.__setattr__(self, "maximum_productivity", Fraction(self.maximum_productivity))


class RecipeCatalog:
    """Supported item recipes indexed by recipe name and product item."""

    def __init__(self, recipes: Sequence[ItemRecipe]) -> None:
        by_name: dict[str, ItemRecipe] = {}
        producers: dict[str, list[ItemRecipe]] = {}
        for recipe in recipes:
            if recipe.name in by_name:
                raise ValueError(f"duplicate recipe name: {recipe.name}")
            by_name[recipe.name] = recipe
            producers.setdefault(recipe.product, []).append(recipe)

        self._recipes = tuple(sorted(by_name.values(), key=lambda recipe: recipe.name))
        self._by_name = MappingProxyType(by_name)
        self._producers = MappingProxyType(
            {
                item: tuple(sorted(values, key=lambda recipe: recipe.name))
                for item, values in producers.items()
            }
        )

    @property
    def recipes(self) -> tuple[ItemRecipe, ...]:
        return self._recipes

    def recipe_named(self, name: str) -> ItemRecipe:
        try:
            return self._by_name[name]
        except KeyError as exc:
            raise KeyError(f"unknown recipe: {name}") from exc

    def producers_of(self, item: str) -> tuple[ItemRecipe, ...]:
        return self._producers.get(item, ())

    def select_producer(
        self,
        item: str,
        *,
        overrides: Mapping[str, str] | None = None,
    ) -> ItemRecipe:
        """Select the canonical producer for ``item``.

        Policy is deliberately small and deterministic:

        1. An explicit ``item -> recipe-name`` override wins.
        2. If exactly one supported recipe produces the item, use it.
        3. If several recipes exist and exactly one has the same prototype name as
           the item, use that conventional Factorio recipe.
        4. Otherwise require an override.

        This keeps multi-recipe policy outside the graph core while making ordinary
        vanilla-style recipe sets convenient.
        """

        if overrides and item in overrides:
            recipe_name = overrides[item]
            try:
                recipe = self.recipe_named(recipe_name)
            except KeyError as exc:
                raise InvalidRecipeOverrideError(
                    f"override for {item!r} names unknown recipe {recipe_name!r}"
                ) from exc
            if recipe.product != item:
                raise InvalidRecipeOverrideError(
                    f"override recipe {recipe_name!r} produces {recipe.product!r}, not {item!r}"
                )
            return recipe

        producers = self.producers_of(item)
        if not producers:
            raise MissingProducerError(f"no supported recipe produces {item!r}")
        if len(producers) == 1:
            return producers[0]

        same_name = tuple(recipe for recipe in producers if recipe.name == item)
        if len(same_name) == 1:
            return same_name[0]

        names = ", ".join(recipe.name for recipe in producers)
        raise AmbiguousProducerError(
            f"multiple recipes produce {item!r}: {names}; configure an override"
        )


@dataclass(frozen=True)
class RecipeDAG:
    """Canonical dependency DAG ordered from raw-side recipes to final recipes."""

    targets: tuple[str, ...]
    raw_items: frozenset[str]
    recipes: tuple[ItemRecipe, ...]

    def __post_init__(self) -> None:
        by_product = {recipe.product: recipe for recipe in self.recipes}
        object.__setattr__(self, "_by_product", MappingProxyType(by_product))

    @property
    def produced_items(self) -> frozenset[str]:
        return frozenset(recipe.product for recipe in self.recipes)

    @property
    def required_items(self) -> frozenset[str]:
        items = set(self.targets) | set(self.raw_items)
        for recipe in self.recipes:
            items.add(recipe.product)
            items.update(recipe.ingredients)
        return frozenset(items)

    def recipe_for(self, item: str) -> ItemRecipe | None:
        return self._by_product.get(item)

    def to_json_dict(self) -> dict[str, object]:
        """Return a stable JSON-friendly representation for inspection/tooling."""

        return {
            "targets": list(self.targets),
            "raw_items": sorted(self.raw_items),
            "recipes": [
                {
                    "name": recipe.name,
                    "product": recipe.product,
                    "product_amount": str(recipe.product_amount),
                    "ingredients": {
                        item: str(amount)
                        for item, amount in sorted(recipe.ingredients.items())
                    },
                    "category": recipe.category,
                    "energy_required": str(recipe.energy_required),
                    "allow_productivity": recipe.allow_productivity,
                    "allow_quality": recipe.allow_quality,
                    "maximum_productivity": str(recipe.maximum_productivity),
                }
                for recipe in self.recipes
            ],
        }


def build_recipe_dag(
    catalog: RecipeCatalog,
    *,
    targets: Sequence[str],
    raw_items: set[str] | frozenset[str],
    overrides: Mapping[str, str] | None = None,
) -> RecipeDAG:
    """Resolve the canonical recipe ancestry of ``targets`` up to ``raw_items``.

    The traversal is strict: an unproducible non-raw ingredient is a configuration
    error, and a selected producer cycle is reported explicitly. Recipes are returned
    in topological execution order (ingredients before consumers).
    """

    target_tuple = tuple(dict.fromkeys(targets))
    if not target_tuple:
        raise ValueError("at least one target item is required")
    raw = frozenset(raw_items)
    selected: dict[str, ItemRecipe] = {}
    visiting: list[str] = []
    visited: set[str] = set()
    ordered: list[ItemRecipe] = []

    def visit(item: str) -> None:
        if item in raw or item in visited:
            return
        if item in visiting:
            start = visiting.index(item)
            cycle = visiting[start:] + [item]
            raise RecipeCycleError("canonical recipe cycle: " + " -> ".join(cycle))

        visiting.append(item)
        try:
            recipe = catalog.select_producer(item, overrides=overrides)
            selected[item] = recipe
            for ingredient in sorted(recipe.ingredients):
                visit(ingredient)
            ordered.append(recipe)
            visited.add(item)
        finally:
            visiting.pop()

    for target in target_tuple:
        visit(target)

    # ``selected`` is retained while traversing mainly to make the one-producer-per-
    # item invariant obvious. ``ordered`` is already deduplicated by ``visited``.
    assert len(ordered) == len(selected)
    return RecipeDAG(targets=target_tuple, raw_items=raw, recipes=tuple(ordered))
