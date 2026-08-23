"""Translate deterministic scheduler jobs into the physical worker-pool offer ABI.

The offline scheduler is allowed to model bounded multi-craft batches, while the validated physical
worker protocol deliberately accepts exactly one craft per four-phase envelope. This bridge keeps
that distinction explicit: physical dispatch requires ``max_batch_crafts == 1`` and maps each
scheduler job one-to-one to one worker offer.

Recipe selection uses an explicit Factorio ``recipe`` signal rather than an item signal. Factorio's
Set-recipe logic gives recipe signals priority, avoiding ambiguity when several recipes produce the
same item.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from types import MappingProxyType

from factorio_circuit import SignalId

from .scheduler import DeterministicMallScheduler, DispatchPlan, MallJob


@dataclass(frozen=True, slots=True)
class WorkerOffer:
    """Stable payload vectors for one accepted one-craft worker transaction."""

    recipe: Mapping[SignalId, int]
    inputs: Mapping[SignalId, int]
    product: Mapping[SignalId, int]

    def __post_init__(self) -> None:
        recipe = dict(self.recipe)
        inputs = {signal: value for signal, value in self.inputs.items() if value}
        product = {signal: value for signal, value in self.product.items() if value}
        if len(recipe) != 1:
            raise ValueError("worker offer requires exactly one recipe signal")
        recipe_signal, recipe_value = next(iter(recipe.items()))
        if recipe_signal.kind != "recipe" or recipe_value <= 0:
            raise ValueError("worker offer recipe must be one positive recipe signal")
        if any(value < 0 for value in inputs.values()):
            raise ValueError("worker offer inputs must be non-negative")
        if not product or any(value <= 0 for value in product.values()):
            raise ValueError("worker offer product vector must be positive and nonempty")
        object.__setattr__(self, "recipe", MappingProxyType(recipe))
        object.__setattr__(self, "inputs", MappingProxyType(inputs))
        object.__setattr__(self, "product", MappingProxyType(product))

    def semantic_inputs(self, *, valid: int) -> dict[str, object]:
        """Render one row for the worker-pool semantic input ABI."""

        if valid not in {0, 1}:
            raise ValueError("worker offer valid level must be 0 or 1")
        return {
            "offer_valid": valid,
            "offer_recipe": dict(self.recipe),
            "offer_inputs": dict(self.inputs),
            "offer_product": dict(self.product),
        }


@dataclass(frozen=True, slots=True)
class WorkerDispatchPlan:
    """Scheduler plan plus its one-to-one physical worker envelopes."""

    scheduler_plan: DispatchPlan
    offers: tuple[WorkerOffer, ...]


def _integer_item_amount(value: Fraction, *, context: str) -> int:
    amount = Fraction(value)
    if amount.denominator != 1:
        raise ValueError(
            f"{context} amount {amount} is not an integer item count; "
            "the current physical mall bridge supports item-only recipes"
        )
    return amount.numerator


def job_to_worker_offer(job: MallJob) -> WorkerOffer:
    """Encode one single-craft deterministic job as recipe/input/product signal vectors."""

    if job.crafts != 1:
        raise ValueError(
            "physical worker offers are exactly one craft; configure scheduler max_batch_crafts=1"
        )

    inputs = {
        SignalId("item", item): _integer_item_amount(amount, context=f"input {item!r}")
        for item, amount in job.inputs.items()
        if amount
    }
    product_amount = _integer_item_amount(
        job.product_amount,
        context=f"product {job.product!r}",
    )
    return WorkerOffer(
        recipe={SignalId("recipe", job.recipe_name): 1},
        inputs=inputs,
        product={SignalId("item", job.product): product_amount},
    )


def plan_worker_offers(
    scheduler: DeterministicMallScheduler,
    *,
    targets: Mapping[str, Fraction],
    stock: Mapping[str, Fraction],
    active_jobs: Sequence[MallJob] = (),
) -> WorkerDispatchPlan:
    """Plan immediately dispatchable work and encode every new job for the physical pool.

    ``max_batch_crafts=1`` is a correctness requirement, not merely a tuning preference. The
    scheduler reserves/promises an entire planned job immediately, whereas the physical worker
    ledger begins only when its one-craft envelope is accepted. Allowing a multi-craft scheduler job
    to be silently split here would therefore create an accounting interval with incompatible
    ledgers.
    """

    if scheduler.max_batch_crafts != 1:
        raise ValueError("physical worker dispatch requires scheduler max_batch_crafts=1")
    if any(job.crafts != 1 for job in active_jobs):
        raise ValueError("physical active jobs must each represent exactly one craft")

    plan = scheduler.plan(targets=targets, stock=stock, active_jobs=active_jobs)
    offers = tuple(job_to_worker_offer(job) for job in plan.jobs)
    return WorkerDispatchPlan(plan, offers)
