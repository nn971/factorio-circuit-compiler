from fractions import Fraction

from examples.autonomous_mall.recipe_graph import ItemRecipe, RecipeCatalog
from examples.autonomous_mall.scheduler import DeterministicMallScheduler, MallJob, complete_jobs


def recipe(name, product, amount=1, **ingredients):
    return ItemRecipe(name=name, product=product, product_amount=Fraction(amount), ingredients=ingredients)


def test_infers_external_inputs_and_splits_batch_across_workers():
    catalog = RecipeCatalog([recipe("gear", "gear", plate=2)])
    scheduler = DeterministicMallScheduler(catalog, worker_count=2, max_batch_crafts=2)

    plan = scheduler.plan(targets={"gear": 4}, stock={"plate": 8})

    assert [(job.recipe_name, job.crafts) for job in plan.jobs] == [("gear", 2), ("gear", 2)]
    assert plan.blocked_external == {}
    assert plan.reserved_after_dispatch == {"plate": Fraction(8)}
    assert plan.promised_after_dispatch == {"gear": Fraction(4)}


def test_dispatches_missing_intermediate_before_blocked_consumer():
    catalog = RecipeCatalog(
        [
            recipe("cable", "cable", amount=2, copper=1),
            recipe("circuit", "circuit", iron=1, cable=3),
        ]
    )
    scheduler = DeterministicMallScheduler(catalog, worker_count=2, max_batch_crafts=2)

    plan = scheduler.plan(targets={"circuit": 2}, stock={"iron": 2, "copper": 3})

    assert [(job.recipe_name, job.crafts) for job in plan.jobs] == [("cable", 2), ("cable", 1)]
    assert plan.planned_crafts == {"circuit": 2, "cable": 3}
    assert plan.blocked_external == {}


def test_active_promised_output_prevents_duplicate_dispatch():
    catalog = RecipeCatalog([recipe("gear", "gear", plate=2)])
    scheduler = DeterministicMallScheduler(catalog, worker_count=2)
    active = MallJob.from_recipe(catalog.recipe_named("gear"), 10)

    plan = scheduler.plan(targets={"gear": 10}, stock={"plate": 20}, active_jobs=[active])

    assert plan.jobs == ()
    assert plan.planned_crafts == {}
    assert plan.reserved_after_dispatch == {"plate": Fraction(20)}
    assert plan.promised_after_dispatch == {"gear": Fraction(10)}


def test_active_reservations_prevent_overcommit():
    catalog = RecipeCatalog(
        [
            recipe("a", "a", iron=1),
            recipe("b", "b", iron=1),
        ]
    )
    scheduler = DeterministicMallScheduler(catalog, worker_count=2, max_batch_crafts=10)
    active = MallJob.from_recipe(catalog.recipe_named("a"), 6)

    plan = scheduler.plan(targets={"a": 6, "b": 5}, stock={"iron": 10}, active_jobs=[active])

    assert [(job.recipe_name, job.crafts) for job in plan.jobs] == [("b", 4)]
    assert plan.blocked_external == {"iron": Fraction(1)}
    assert plan.reserved_after_dispatch == {"iron": Fraction(10)}


def test_replenishes_intermediate_that_is_also_a_target():
    catalog = RecipeCatalog(
        [
            recipe("gear", "gear", iron=2),
            recipe("machine", "machine", gear=1),
        ]
    )
    scheduler = DeterministicMallScheduler(catalog, worker_count=2)

    plan = scheduler.plan(
        targets={"gear": 100, "machine": 1},
        stock={"gear": 100, "iron": 2},
    )

    assert plan.planned_crafts == {"machine": 1, "gear": 1}
    assert {job.recipe_name for job in plan.jobs} == {"gear", "machine"}


def test_unproducible_target_is_reported_as_external_shortage():
    scheduler = DeterministicMallScheduler(RecipeCatalog([]), worker_count=3)

    plan = scheduler.plan(targets={"iron": 100}, stock={"iron": 25})

    assert plan.jobs == ()
    assert plan.blocked_external == {"iron": Fraction(75)}


def test_multiple_completions_are_accounted_together():
    catalog = RecipeCatalog([recipe("gear", "gear", plate=2)])
    first = MallJob.from_recipe(catalog.recipe_named("gear"), 2)
    second = MallJob.from_recipe(catalog.recipe_named("gear"), 3)

    stock = complete_jobs({"plate": 10}, [first, second])

    assert stock == {"gear": Fraction(5)}


def test_rejects_snapshot_smaller_than_active_reservations():
    catalog = RecipeCatalog([recipe("gear", "gear", plate=2)])
    scheduler = DeterministicMallScheduler(catalog, worker_count=1)
    active = MallJob.from_recipe(catalog.recipe_named("gear"), 3)

    try:
        scheduler.plan(targets={"gear": 3}, stock={"plate": 5}, active_jobs=[active])
    except ValueError as exc:
        assert "active reservations" in str(exc)
    else:
        raise AssertionError("expected an inconsistent stock snapshot to be rejected")
