"""First deterministic multi-worker scheduler prototype for the autonomous mall.

This module deliberately ignores quality, productivity policy, crafting time, and raw-material
optimization. Items without a supported producer are inferred to be external inputs. The scheduler
is an offline/reference policy: it turns one observed stock snapshot plus the set of already active
jobs into a set of immediately dispatchable jobs for an anonymous worker pool.

``stock`` has one important prototype-level contract: it is the mall-owned physical inventory before
subtracting reservations held by ``active_jobs``. A future circuit integration can reconstruct this
quantity from the roboport view plus worker-local holdings if the Factorio device boundary requires
it. Active-job inputs remain reserved until completion.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from types import MappingProxyType
from typing import Mapping, Sequence

from .recipe_graph import ItemRecipe, RecipeCatalog, RecipeCycleError

Amount = Fraction


def _amounts(values: Mapping[str, Amount]) -> dict[str, Amount]:
    return {
        item: Fraction(amount)
        for item, amount in values.items()
        if Fraction(amount) != 0
    }


def _ceil_fraction(value: Amount) -> int:
    value = Fraction(value)
    return (value.numerator + value.denominator - 1) // value.denominator


@dataclass(frozen=True)
class MallJob:
    """One bounded deterministic batch promised to an anonymous worker."""

    recipe_name: str
    crafts: int
    product: str
    product_amount: Amount
    inputs: Mapping[str, Amount]

    def __post_init__(self) -> None:
        if self.crafts <= 0:
            raise ValueError("crafts must be positive")
        product_amount = Fraction(self.product_amount)
        if product_amount <= 0:
            raise ValueError("product_amount must be positive")
        inputs = _amounts(self.inputs)
        if any(amount < 0 for amount in inputs.values()):
            raise ValueError("job input amounts must be non-negative")
        object.__setattr__(self, "product_amount", product_amount)
        object.__setattr__(self, "inputs", MappingProxyType(inputs))

    @classmethod
    def from_recipe(cls, recipe: ItemRecipe, crafts: int) -> MallJob:
        return cls(
            recipe_name=recipe.name,
            crafts=crafts,
            product=recipe.product,
            product_amount=recipe.product_amount * crafts,
            inputs={item: amount * crafts for item, amount in recipe.ingredients.items()},
        )


@dataclass(frozen=True)
class DispatchPlan:
    """Jobs that can start now plus diagnostic information about the full production need."""

    jobs: tuple[MallJob, ...]
    planned_crafts: Mapping[str, int]
    blocked_external: Mapping[str, Amount]
    reserved_after_dispatch: Mapping[str, Amount]
    promised_after_dispatch: Mapping[str, Amount]

    def __post_init__(self) -> None:
        object.__setattr__(self, "planned_crafts", MappingProxyType(dict(self.planned_crafts)))
        object.__setattr__(self, "blocked_external", MappingProxyType(_amounts(self.blocked_external)))
        object.__setattr__(
            self,
            "reserved_after_dispatch",
            MappingProxyType(_amounts(self.reserved_after_dispatch)),
        )
        object.__setattr__(
            self,
            "promised_after_dispatch",
            MappingProxyType(_amounts(self.promised_after_dispatch)),
        )


class DeterministicMallScheduler:
    """Reference scheduler for deterministic recipes and an anonymous worker pool."""

    def __init__(
        self,
        catalog: RecipeCatalog,
        *,
        worker_count: int,
        max_batch_crafts: int = 64,
        recipe_overrides: Mapping[str, str] | None = None,
    ) -> None:
        if worker_count <= 0:
            raise ValueError("worker_count must be positive")
        if max_batch_crafts <= 0:
            raise ValueError("max_batch_crafts must be positive")
        self._catalog = catalog
        self.worker_count = worker_count
        self.max_batch_crafts = max_batch_crafts
        self._recipe_overrides = dict(recipe_overrides or {})

    def _producer(self, item: str) -> ItemRecipe | None:
        if not self._catalog.producers_of(item):
            return None
        return self._catalog.select_producer(item, overrides=self._recipe_overrides)

    @staticmethod
    def _active_ledgers(
        active_jobs: Sequence[MallJob],
    ) -> tuple[dict[str, Amount], dict[str, Amount]]:
        reserved: dict[str, Amount] = defaultdict(Fraction)
        promised: dict[str, Amount] = defaultdict(Fraction)
        for job in active_jobs:
            for item, amount in job.inputs.items():
                reserved[item] += amount
            promised[job.product] += job.product_amount
        return dict(reserved), dict(promised)

    def _expand_production_need(
        self,
        *,
        targets: Mapping[str, Amount],
        stock: Mapping[str, Amount],
        reserved: Mapping[str, Amount],
        promised: Mapping[str, Amount],
    ) -> tuple[dict[str, int], dict[str, Amount], dict[str, ItemRecipe]]:
        supply: dict[str, Amount] = defaultdict(Fraction)
        for item, amount in _amounts(stock).items():
            supply[item] += amount
        for item, amount in reserved.items():
            supply[item] -= amount
        for item, amount in promised.items():
            supply[item] += amount

        planned_crafts: dict[str, int] = defaultdict(int)
        blocked_external: dict[str, Amount] = defaultdict(Fraction)
        selected_recipes: dict[str, ItemRecipe] = {}
        visiting: list[str] = []

        def ensure(item: str, amount: Amount) -> None:
            amount = Fraction(amount)
            if amount <= 0:
                return

            available = max(supply[item], Fraction(0))
            used = min(available, amount)
            supply[item] -= used
            shortage = amount - used
            if shortage <= 0:
                return

            recipe = self._producer(item)
            if recipe is None:
                blocked_external[item] += shortage
                return
            if item in visiting:
                start = visiting.index(item)
                cycle = visiting[start:] + [item]
                raise RecipeCycleError("canonical recipe cycle: " + " -> ".join(cycle))

            crafts = _ceil_fraction(shortage / recipe.product_amount)
            planned_crafts[recipe.name] += crafts
            selected_recipes[recipe.name] = recipe

            visiting.append(item)
            try:
                for ingredient, per_craft in sorted(recipe.ingredients.items()):
                    ensure(ingredient, per_craft * crafts)
            finally:
                visiting.pop()

            produced = recipe.product_amount * crafts
            supply[item] += produced - shortage

        for item, amount in sorted(_amounts(targets).items()):
            if amount < 0:
                raise ValueError("target amounts must be non-negative")
            ensure(item, amount)

        return dict(planned_crafts), dict(blocked_external), selected_recipes

    def _recipe_depth(
        self,
        recipe: ItemRecipe,
        selected_recipes: Mapping[str, ItemRecipe],
        cache: dict[str, int],
        visiting: set[str],
    ) -> int:
        if recipe.name in cache:
            return cache[recipe.name]
        if recipe.name in visiting:
            raise RecipeCycleError(f"canonical recipe cycle includes {recipe.product!r}")
        visiting.add(recipe.name)
        try:
            parent_depths: list[int] = []
            for ingredient in recipe.ingredients:
                producer = self._producer(ingredient)
                if producer is not None and producer.name in selected_recipes:
                    parent_depths.append(
                        self._recipe_depth(producer, selected_recipes, cache, visiting)
                    )
            depth = 1 + max(parent_depths, default=0)
            cache[recipe.name] = depth
            return depth
        finally:
            visiting.remove(recipe.name)

    @staticmethod
    def _max_feasible_crafts(recipe: ItemRecipe, available: Mapping[str, Amount]) -> int | None:
        bound: int | None = None
        for item, per_craft in recipe.ingredients.items():
            if per_craft <= 0:
                continue
            item_bound = int(Fraction(available.get(item, 0)) // per_craft)
            bound = item_bound if bound is None else min(bound, item_bound)
        return bound

    def plan(
        self,
        *,
        targets: Mapping[str, Amount],
        stock: Mapping[str, Amount],
        active_jobs: Sequence[MallJob] = (),
    ) -> DispatchPlan:
        """Plan and reserve as many immediately startable batches as free workers allow."""

        if len(active_jobs) > self.worker_count:
            raise ValueError("active job count exceeds worker_count")
        if any(Fraction(amount) < 0 for amount in stock.values()):
            raise ValueError("stock amounts must be non-negative")

        active_reserved, active_promised = self._active_ledgers(active_jobs)
        for item, amount in active_reserved.items():
            if amount > Fraction(stock.get(item, 0)):
                raise ValueError(
                    f"active reservations for {item!r} exceed the supplied stock snapshot"
                )

        planned_crafts, blocked_external, selected_recipes = self._expand_production_need(
            targets=targets,
            stock=stock,
            reserved=active_reserved,
            promised=active_promised,
        )

        available_now: dict[str, Amount] = defaultdict(Fraction)
        for item, amount in _amounts(stock).items():
            available_now[item] += amount
        for item, amount in active_reserved.items():
            available_now[item] -= amount

        free_workers = self.worker_count - len(active_jobs)
        remaining_crafts = dict(planned_crafts)
        new_jobs: list[MallJob] = []
        new_reserved: dict[str, Amount] = defaultdict(Fraction)
        new_promised: dict[str, Amount] = defaultdict(Fraction)

        depth_cache: dict[str, int] = {}
        ordered_recipes = sorted(
            selected_recipes.values(),
            key=lambda recipe: (
                self._recipe_depth(recipe, selected_recipes, depth_cache, set()),
                recipe.name,
            ),
        )

        while len(new_jobs) < free_workers:
            dispatched = False
            for recipe in ordered_recipes:
                remaining = remaining_crafts.get(recipe.name, 0)
                if remaining <= 0:
                    continue
                feasible = self._max_feasible_crafts(recipe, available_now)
                if feasible == 0:
                    continue
                crafts = min(remaining, self.max_batch_crafts)
                if feasible is not None:
                    crafts = min(crafts, feasible)
                if crafts <= 0:
                    continue

                job = MallJob.from_recipe(recipe, crafts)
                new_jobs.append(job)
                remaining_crafts[recipe.name] -= crafts
                for item, amount in job.inputs.items():
                    available_now[item] -= amount
                    new_reserved[item] += amount
                new_promised[job.product] += job.product_amount
                dispatched = True
                break
            if not dispatched:
                break

        reserved_after = defaultdict(Fraction, active_reserved)
        promised_after = defaultdict(Fraction, active_promised)
        for item, amount in new_reserved.items():
            reserved_after[item] += amount
        for item, amount in new_promised.items():
            promised_after[item] += amount

        return DispatchPlan(
            jobs=tuple(new_jobs),
            planned_crafts=planned_crafts,
            blocked_external=blocked_external,
            reserved_after_dispatch=dict(reserved_after),
            promised_after_dispatch=dict(promised_after),
        )


def complete_jobs(
    stock: Mapping[str, Amount],
    jobs: Sequence[MallJob],
) -> dict[str, Amount]:
    """Reference inventory update for one or several deterministic job completions."""

    updated: dict[str, Amount] = defaultdict(Fraction)
    for item, amount in _amounts(stock).items():
        updated[item] += amount
    for job in jobs:
        for item, amount in job.inputs.items():
            updated[item] -= amount
            if updated[item] < 0:
                raise ValueError(f"completion consumes more {item!r} than stock contains")
        updated[job.product] += job.product_amount
    return {item: amount for item, amount in updated.items() if amount != 0}
