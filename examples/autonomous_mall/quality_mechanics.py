"""Exact expected-value helpers for Factorio quality and recycling mechanics."""

from __future__ import annotations

from fractions import Fraction
from typing import Mapping

from .model import Amount, Commodity, Quality


def quality_distribution(base: Quality, quality_chance: Amount) -> dict[Quality, Amount]:
    """Return the exact output-quality distribution for one produced item.

    ``quality_chance`` is the machine's total initial quality chance as a fraction in
    ``[0, 1]``. After the initial quality roll succeeds, each additional upgrade uses
    Factorio's fixed 10% continuation chance. Probability mass that would advance
    beyond legendary accumulates at legendary.
    """

    q = Fraction(quality_chance)
    if q < 0 or q > 1:
        raise ValueError("quality_chance must lie in [0, 1]")
    if base is Quality.LEGENDARY:
        return {Quality.LEGENDARY: Fraction(1)}

    result: dict[Quality, Amount] = {base: Fraction(1) - q}
    remaining_tiers = int(Quality.LEGENDARY - base)
    for jump in range(1, remaining_tiers + 1):
        target = Quality(int(base) + jump)
        if jump == remaining_tiers:
            probability = q / (10 ** (jump - 1))
        else:
            probability = q * 9 / (10**jump)
        result[target] = probability
    return {quality: probability for quality, probability in result.items() if probability}


def expected_quality_outputs(
    *,
    item: str,
    base_quality: Quality,
    output_amount: Amount,
    quality_chance: Amount,
) -> dict[Commodity, Amount]:
    """Expected quality-qualified outputs of a craft producing one item type."""

    amount = Fraction(output_amount)
    if amount < 0:
        raise ValueError("output_amount must be non-negative")
    return {
        Commodity(item, quality): amount * probability
        for quality, probability in quality_distribution(base_quality, quality_chance).items()
    }


def expected_recycler_outputs(
    *,
    ingredients_per_recipe: Mapping[str, Amount],
    recipe_output_amount: Amount,
    recycled_quality: Quality,
    quality_chance: Amount,
) -> dict[Commodity, Amount]:
    """Expected solid ingredient return from recycling one product item.

    Factorio returns 25% of the original solid ingredient requirement on average,
    divided by the recipe's product count. Recycler quality modules then apply the
    normal quality-upgrade distribution independently to each returned ingredient.
    """

    outputs = Fraction(recipe_output_amount)
    if outputs <= 0:
        raise ValueError("recipe_output_amount must be positive")
    distribution = quality_distribution(recycled_quality, quality_chance)
    result: dict[Commodity, Amount] = {}
    for item, ingredient_amount in ingredients_per_recipe.items():
        returned = Fraction(ingredient_amount) / outputs / 4
        if returned < 0:
            raise ValueError("ingredient amounts must be non-negative")
        for quality, probability in distribution.items():
            commodity = Commodity(item, quality)
            result[commodity] = result.get(commodity, Fraction(0)) + returned * probability
    return {commodity: amount for commodity, amount in result.items() if amount}
