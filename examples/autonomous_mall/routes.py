"""Build expected mall routes from no-fluid assembler recipes."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Mapping

from .model import Amount, Commodity, ProductionRoute, Quality, WorkerKind
from .quality_mechanics import expected_quality_outputs, expected_recycler_outputs


@dataclass(frozen=True)
class ItemRecipe:
    """Single-item-output assembler recipe used by the first mall prototype."""

    name: str
    product: str
    product_amount: Amount
    ingredients: Mapping[str, Amount]

    def __post_init__(self) -> None:
        if Fraction(self.product_amount) <= 0:
            raise ValueError("product_amount must be positive")
        if any(Fraction(amount) < 0 for amount in self.ingredients.values()):
            raise ValueError("ingredient amounts must be non-negative")


def productivity_route(
    recipe: ItemRecipe,
    *,
    quality: Quality,
    productivity_bonus: Amount,
) -> ProductionRoute:
    """Expected route for one craft on a fixed productivity worker."""

    bonus = Fraction(productivity_bonus)
    if bonus < 0 or bonus > 3:
        raise ValueError("productivity_bonus must lie in [0, 3]")
    inputs = {
        Commodity(item, quality): Fraction(amount)
        for item, amount in recipe.ingredients.items()
    }
    outputs = {
        Commodity(recipe.product, quality): Fraction(recipe.product_amount) * (1 + bonus)
    }
    return ProductionRoute(
        name=f"p:{recipe.name}:{quality.name.lower()}",
        worker_kind=WorkerKind.PRODUCTIVITY,
        inputs=inputs,
        outputs=outputs,
    )


def quality_route(
    recipe: ItemRecipe,
    *,
    base_quality: Quality,
    quality_chance: Amount,
) -> ProductionRoute:
    """Expected route for one craft on a fixed quality worker."""

    inputs = {
        Commodity(item, base_quality): Fraction(amount)
        for item, amount in recipe.ingredients.items()
    }
    outputs = expected_quality_outputs(
        item=recipe.product,
        base_quality=base_quality,
        output_amount=recipe.product_amount,
        quality_chance=quality_chance,
    )
    return ProductionRoute(
        name=f"q:{recipe.name}:{base_quality.name.lower()}",
        worker_kind=WorkerKind.QUALITY,
        inputs=inputs,
        outputs=outputs,
    )


def recycler_route(
    recipe: ItemRecipe,
    *,
    recycled_quality: Quality,
    quality_chance: Amount,
) -> ProductionRoute:
    """Expected route for recycling one product item on a quality recycler."""

    outputs = expected_recycler_outputs(
        ingredients_per_recipe=recipe.ingredients,
        recipe_output_amount=recipe.product_amount,
        recycled_quality=recycled_quality,
        quality_chance=quality_chance,
    )
    return ProductionRoute(
        name=f"r:{recipe.name}:{recycled_quality.name.lower()}",
        worker_kind=WorkerKind.RECYCLER,
        inputs={Commodity(recipe.product, recycled_quality): Fraction(1)},
        outputs=outputs,
    )
