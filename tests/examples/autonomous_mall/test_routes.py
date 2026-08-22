from fractions import Fraction

from examples.autonomous_mall import (
    Commodity,
    ItemRecipe,
    Quality,
    WorkerKind,
    productivity_route,
    quality_route,
    recycler_route,
)


def test_productivity_route_preserves_exact_quality_and_multiplies_output() -> None:
    recipe = ItemRecipe("gear", "iron-gear-wheel", 1, {"iron-plate": 2})
    route = productivity_route(recipe, quality=Quality.RARE, productivity_bonus=Fraction(1, 2))
    assert route.worker_kind is WorkerKind.PRODUCTIVITY
    assert route.inputs == {Commodity("iron-plate", Quality.RARE): 2}
    assert route.outputs == {Commodity("iron-gear-wheel", Quality.RARE): Fraction(3, 2)}


def test_quality_route_exposes_all_quality_outcomes_in_one_output_vector() -> None:
    recipe = ItemRecipe("gear", "iron-gear-wheel", 1, {"iron-plate": 2})
    route = quality_route(recipe, base_quality=Quality.NORMAL, quality_chance=Fraction(1, 5))
    assert route.worker_kind is WorkerKind.QUALITY
    assert route.inputs == {Commodity("iron-plate", Quality.NORMAL): 2}
    assert route.outputs[Commodity("iron-gear-wheel", Quality.NORMAL)] == Fraction(4, 5)
    assert route.outputs[Commodity("iron-gear-wheel", Quality.LEGENDARY)] == Fraction(1, 5000)


def test_recycler_route_is_a_true_multi_output_reverse_transformation() -> None:
    recipe = ItemRecipe("widget", "widget", 2, {"iron-plate": 4, "copper-plate": 2})
    route = recycler_route(recipe, recycled_quality=Quality.NORMAL, quality_chance=0)
    assert route.worker_kind is WorkerKind.RECYCLER
    assert route.inputs == {Commodity("widget", Quality.NORMAL): 1}
    assert route.outputs == {
        Commodity("iron-plate", Quality.NORMAL): Fraction(1, 2),
        Commodity("copper-plate", Quality.NORMAL): Fraction(1, 4),
    }
