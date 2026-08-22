from fractions import Fraction

from examples.autonomous_mall.model import Commodity, Quality
from examples.autonomous_mall.quality_mechanics import (
    expected_quality_outputs,
    expected_recycler_outputs,
    quality_distribution,
)


def test_quality_distribution_matches_factorio_continuation_rule() -> None:
    distribution = quality_distribution(Quality.NORMAL, Fraction(1, 5))

    assert distribution == {
        Quality.NORMAL: Fraction(4, 5),
        Quality.UNCOMMON: Fraction(18, 100),
        Quality.RARE: Fraction(18, 1000),
        Quality.EPIC: Fraction(18, 10000),
        Quality.LEGENDARY: Fraction(2, 10000),
    }
    assert sum(distribution.values()) == 1


def test_quality_distribution_caps_probability_at_legendary() -> None:
    distribution = quality_distribution(Quality.EPIC, Fraction(1, 4))
    assert distribution == {Quality.EPIC: Fraction(3, 4), Quality.LEGENDARY: Fraction(1, 4)}


def test_expected_quality_outputs_keep_quality_in_commodity_identity() -> None:
    outputs = expected_quality_outputs(
        item="iron-gear-wheel",
        base_quality=Quality.RARE,
        output_amount=2,
        quality_chance=Fraction(1, 5),
    )

    assert outputs[Commodity("iron-gear-wheel", Quality.RARE)] == Fraction(8, 5)
    assert outputs[Commodity("iron-gear-wheel", Quality.EPIC)] == Fraction(9, 25)
    assert outputs[Commodity("iron-gear-wheel", Quality.LEGENDARY)] == Fraction(1, 25)


def test_recycler_returns_quarter_of_recipe_ingredients_in_expectation() -> None:
    outputs = expected_recycler_outputs(
        ingredients_per_recipe={"iron-plate": 4, "copper-plate": 2},
        recipe_output_amount=2,
        recycled_quality=Quality.NORMAL,
        quality_chance=0,
    )

    assert outputs == {
        Commodity("iron-plate", Quality.NORMAL): Fraction(1, 2),
        Commodity("copper-plate", Quality.NORMAL): Fraction(1, 4),
    }


def test_recycler_quality_modules_upgrade_returned_ingredients() -> None:
    outputs = expected_recycler_outputs(
        ingredients_per_recipe={"iron-plate": 4},
        recipe_output_amount=1,
        recycled_quality=Quality.RARE,
        quality_chance=Fraction(1, 5),
    )

    assert outputs == {
        Commodity("iron-plate", Quality.RARE): Fraction(4, 5),
        Commodity("iron-plate", Quality.EPIC): Fraction(9, 50),
        Commodity("iron-plate", Quality.LEGENDARY): Fraction(1, 50),
    }
