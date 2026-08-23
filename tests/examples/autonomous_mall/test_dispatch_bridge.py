from fractions import Fraction

import pytest

from examples.autonomous_mall.dispatch_bridge import (
    job_to_worker_offer,
    plan_worker_offers,
)
from examples.autonomous_mall.recipe_graph import ItemRecipe, RecipeCatalog
from examples.autonomous_mall.scheduler import DeterministicMallScheduler, MallJob
from factorio_circuit import SignalId


def _recipe(name, product, amount=1, **ingredients):
    return ItemRecipe(
        name=name,
        product=product,
        product_amount=Fraction(amount),
        ingredients=ingredients,
    )


def test_single_craft_job_uses_explicit_recipe_signal_and_exact_vectors() -> None:
    recipe = _recipe(
        "iron-gear-wheel",
        "iron-gear-wheel",
        **{"iron-plate": 2},
    )
    job = MallJob.from_recipe(recipe, 1)

    offer = job_to_worker_offer(job)

    assert offer.recipe == {SignalId("recipe", "iron-gear-wheel"): 1}
    assert offer.inputs == {SignalId("item", "iron-plate"): 2}
    assert offer.product == {SignalId("item", "iron-gear-wheel"): 1}
    assert offer.semantic_inputs(valid=1) == {
        "offer_valid": 1,
        "offer_recipe": {SignalId("recipe", "iron-gear-wheel"): 1},
        "offer_inputs": {SignalId("item", "iron-plate"): 2},
        "offer_product": {SignalId("item", "iron-gear-wheel"): 1},
    }


def test_bridge_requires_one_craft_scheduler_jobs() -> None:
    catalog = RecipeCatalog([_recipe("gear", "gear", plate=2)])
    scheduler = DeterministicMallScheduler(catalog, worker_count=2, max_batch_crafts=2)

    with pytest.raises(ValueError, match="max_batch_crafts=1"):
        plan_worker_offers(scheduler, targets={"gear": 2}, stock={"plate": 4})


def test_single_craft_scheduler_maps_jobs_one_to_one_to_worker_offers() -> None:
    catalog = RecipeCatalog([_recipe("gear", "gear", plate=2)])
    scheduler = DeterministicMallScheduler(catalog, worker_count=2, max_batch_crafts=1)

    dispatch = plan_worker_offers(
        scheduler,
        targets={"gear": 2},
        stock={"plate": 4},
    )

    assert [(job.recipe_name, job.crafts) for job in dispatch.scheduler_plan.jobs] == [
        ("gear", 1),
        ("gear", 1),
    ]
    assert len(dispatch.offers) == 2
    assert all(offer.recipe == {SignalId("recipe", "gear"): 1} for offer in dispatch.offers)
    assert dispatch.scheduler_plan.reserved_after_dispatch == {"plate": Fraction(4)}
    assert dispatch.scheduler_plan.promised_after_dispatch == {"gear": Fraction(2)}


def test_bridge_rejects_fractional_item_counts() -> None:
    job = MallJob(
        recipe_name="fractional",
        crafts=1,
        product="thing",
        product_amount=Fraction(1),
        inputs={"dust": Fraction(1, 2)},
    )

    with pytest.raises(ValueError, match="not an integer item count"):
        job_to_worker_offer(job)
