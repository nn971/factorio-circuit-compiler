from fractions import Fraction

import pytest

from examples.autonomous_mall import (
    Commodity,
    MaterialPlanner,
    NoRouteError,
    ProductionRoute,
    Quality,
    RecipeBook,
    WorkerKind,
)


def c(item: str, quality: Quality = Quality.NORMAL) -> Commodity:
    return Commodity(item, quality)


def route(
    name: str,
    kind: WorkerKind,
    inputs: dict[Commodity, object],
    outputs: dict[Commodity, object],
) -> ProductionRoute:
    return ProductionRoute(name, kind, inputs, outputs)


def test_raw_mask_changes_selected_route_without_changing_recipe_book() -> None:
    plate = c("iron-plate")
    circuit = c("electronic-circuit")
    gear = c("iron-gear-wheel")
    machine = c("assembling-machine")
    book = RecipeBook(
        [
            route("machine-via-gears", WorkerKind.PRODUCTIVITY, {gear: 2}, {machine: 1}),
            route(
                "machine-via-circuits",
                WorkerKind.PRODUCTIVITY,
                {circuit: 1},
                {machine: 1},
            ),
            route("gear", WorkerKind.PRODUCTIVITY, {plate: 2}, {gear: 1}),
            route("circuit", WorkerKind.PRODUCTIVITY, {plate: 10}, {circuit: 1}),
        ]
    )

    plates_raw = MaterialPlanner(book, raw_items={"iron-plate"}).plan(
        targets={machine: 1}, stock={}
    )
    circuits_raw = MaterialPlanner(book, raw_items={"electronic-circuit", "iron-plate"}).plan(
        targets={machine: 1}, stock={circuit: 1}
    )

    assert plates_raw.raw_required == {plate: 4}
    assert {step.route_name for step in plates_raw.steps} == {"gear", "machine-via-gears"}
    assert circuits_raw.raw_total == 0
    assert [step.route_name for step in circuits_raw.steps] == ["machine-via-circuits"]


def test_existing_non_target_stock_has_zero_marginal_cost() -> None:
    plate = c("iron-plate")
    gear = c("iron-gear-wheel")
    machine = c("assembling-machine")
    book = RecipeBook(
        [
            route("gear", WorkerKind.PRODUCTIVITY, {plate: 2}, {gear: 1}),
            route("machine", WorkerKind.PRODUCTIVITY, {gear: 3}, {machine: 1}),
        ]
    )
    plan = MaterialPlanner(book, raw_items={"iron-plate"}).plan(
        targets={machine: 1}, stock={gear: 3}
    )
    assert plan.raw_total == 0
    assert [step.route_name for step in plan.steps] == ["machine"]


def test_final_demand_stock_is_protected_from_other_targets() -> None:
    plate = c("iron-plate")
    gear = c("iron-gear-wheel")
    machine = c("assembling-machine")
    book = RecipeBook(
        [
            route("gear", WorkerKind.PRODUCTIVITY, {plate: 2}, {gear: 1}),
            route("machine", WorkerKind.PRODUCTIVITY, {gear: 1}, {machine: 1}),
        ]
    )
    plan = MaterialPlanner(book, raw_items={"iron-plate"}).plan(
        targets={gear: 1, machine: 1}, stock={gear: 1}
    )
    assert plan.raw_required == {plate: 2}
    assert {step.route_name for step in plan.steps} == {"gear", "machine"}


def test_quality_and_productivity_routes_are_explicit_alternatives() -> None:
    normal_plate = c("iron-plate")
    rare_plate = c("iron-plate", Quality.RARE)
    rare_gear = c("iron-gear-wheel", Quality.RARE)
    book = RecipeBook(
        [
            route(
                "quality-upcycle-rare-gear",
                WorkerKind.QUALITY,
                {normal_plate: 20},
                {rare_gear: 1},
            ),
            route(
                "productivity-rare-gear",
                WorkerKind.PRODUCTIVITY,
                {rare_plate: Fraction(3, 2)},
                {rare_gear: 1},
            ),
        ]
    )
    plan = MaterialPlanner(book, raw_items={"iron-plate"}).plan(
        targets={rare_gear: 1}, stock={rare_plate: 2}
    )
    assert plan.raw_total == 0
    assert [step.worker_kind for step in plan.steps] == [WorkerKind.PRODUCTIVITY]


def test_expected_route_amounts_use_exact_fractions() -> None:
    plate = c("iron-plate")
    rare_gear = c("iron-gear-wheel", Quality.RARE)
    book = RecipeBook(
        [
            route(
                "expected-quality-policy",
                WorkerKind.QUALITY,
                {plate: Fraction(7, 3)},
                {rare_gear: 1},
            )
        ]
    )
    plan = MaterialPlanner(book, raw_items={"iron-plate"}).plan(
        targets={rare_gear: 2}, stock={}
    )
    assert plan.raw_required == {plate: Fraction(14, 3)}


def test_non_raw_leaf_is_reported_instead_of_silently_treated_as_raw() -> None:
    impossible = c("unknown-intermediate")
    target = c("target")
    book = RecipeBook(
        [route("target", WorkerKind.PRODUCTIVITY, {impossible: 1}, {target: 1})]
    )
    with pytest.raises(NoRouteError):
        MaterialPlanner(book, raw_items={"iron-plate"}).plan(targets={target: 1}, stock={})


def test_route_outputs_can_jointly_satisfy_other_requirements() -> None:
    plate = c("iron-plate")
    scrap = c("scrap")
    target_a = c("target-a")
    target_b = c("target-b")
    book = RecipeBook(
        [
            route("make-a", WorkerKind.QUALITY, {plate: 4}, {target_a: 1, scrap: 2}),
            route("make-b", WorkerKind.PRODUCTIVITY, {scrap: 2}, {target_b: 1}),
        ]
    )
    plan = MaterialPlanner(book, raw_items={"iron-plate"}).plan(
        targets={target_a: 1, target_b: 1}, stock={}
    )
    assert plan.raw_required == {plate: 4}
    assert {step.route_name for step in plan.steps} == {"make-a", "make-b"}


def test_planner_optimizes_joint_targets_instead_of_greedily_per_target() -> None:
    plate = c("iron-plate")
    target_a = c("target-a")
    target_b = c("target-b")
    book = RecipeBook(
        [
            route("a-cheap-alone", WorkerKind.PRODUCTIVITY, {plate: 2}, {target_a: 1}),
            route("a-with-b", WorkerKind.QUALITY, {plate: 3}, {target_a: 1, target_b: 1}),
            route("b-alone", WorkerKind.PRODUCTIVITY, {plate: 2}, {target_b: 1}),
        ]
    )
    plan = MaterialPlanner(book, raw_items={"iron-plate"}).plan(
        targets={target_a: 1, target_b: 1}, stock={}
    )
    assert plan.raw_required == {plate: 3}
    assert {step.route_name for step in plan.steps} == {"a-with-b"}
