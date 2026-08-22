"""Build expected mall routes from no-fluid assembler recipes."""

from __future__ import annotations

from fractions import Fraction

from .model import Amount, Commodity, ProductionRoute, Quality, WorkerKind
from .quality_mechanics import expected_quality_outputs, expected_recycler_outputs
from .recipe_graph import ItemRecipe


def productivity_route(
    recipe: ItemRecipe,
    *,
    quality: Quality,
    productivity_bonus: Amount,
) -> ProductionRoute:
    """Expected route for one craft on a fixed productivity worker."""

    bonus = Fraction(productivity_bonus)
    if bonus < 0 or bonus > recipe.maximum_productivity:
        raise ValueError(
            f"productivity_bonus must lie in [0, {recipe.maximum_productivity}]"
        )
    if bonus and not recipe.allow_productivity:
        raise ValueError(f"recipe {recipe.name!r} does not allow productivity")
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

    chance = Fraction(quality_chance)
    if chance and not recipe.allow_quality:
        raise ValueError(f"recipe {recipe.name!r} does not allow quality")
    inputs = {
        Commodity(item, base_quality): Fraction(amount)
        for item, amount in recipe.ingredients.items()
    }
    outputs = expected_quality_outputs(
        item=recipe.product,
        base_quality=base_quality,
        output_amount=recipe.product_amount,
        quality_chance=chance,
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
