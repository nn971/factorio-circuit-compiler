"""Globally material-optimal expected planner for the autonomous mall example."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Mapping

from .linear import InfeasibleLinearProgram, minimize_covering
from .model import Amount, Commodity, RecipeBook, WorkerKind


class PlanningError(RuntimeError):
    """Base error raised when a requested expected-material plan cannot be formed."""


class NoRouteError(PlanningError):
    """The configured raw boundary and routes cannot satisfy the requested targets."""


@dataclass(frozen=True)
class PlanStep:
    """Expected usage of one production route in the optimal material balance."""

    route_name: str
    worker_kind: WorkerKind
    route_runs: Amount


@dataclass(frozen=True)
class MaterialPlan:
    """Globally optimal expected raw-material plan for one stock/target snapshot."""

    raw_required: Mapping[Commodity, Amount]
    steps: tuple[PlanStep, ...]
    expected_surplus: Mapping[Commodity, Amount]

    @property
    def raw_total(self) -> Amount:
        return sum(self.raw_required.values(), start=Fraction(0))


class MaterialPlanner:
    """Minimize additional expected consumption across a runtime raw-item boundary.

    This is a global material-balance LP over all supplied expected routes. Existing
    stock is a free initial endowment, while final targets remain balance constraints.
    Only configured raw base items receive external-import variables.
    """

    def __init__(self, recipe_book: RecipeBook, *, raw_items: set[str]) -> None:
        self.recipe_book = recipe_book
        self.raw_items = frozenset(raw_items)

    def plan(
        self,
        *,
        targets: Mapping[Commodity, Amount],
        stock: Mapping[Commodity, Amount],
    ) -> MaterialPlan:
        targets = _nonnegative(targets)
        stock = _nonnegative(stock)
        routes = self.recipe_book.routes

        commodities = set(targets) | set(stock)
        for route in routes:
            commodities.update(route.inputs)
            commodities.update(route.outputs)
        ordered = tuple(sorted(commodities))
        row_of = {commodity: index for index, commodity in enumerate(ordered)}

        columns: list[dict[int, Amount]] = []
        costs: list[Amount] = []
        for route in routes:
            column: dict[int, Amount] = {}
            for commodity, amount in route.outputs.items():
                _add(column, row_of[commodity], Fraction(amount))
            for commodity, amount in route.inputs.items():
                _add(column, row_of[commodity], -Fraction(amount))
            columns.append(column)
            costs.append(Fraction(0))

        raw_variables: list[Commodity] = []
        for commodity in ordered:
            if commodity.item not in self.raw_items:
                continue
            columns.append({row_of[commodity]: Fraction(1)})
            costs.append(Fraction(1))
            raw_variables.append(commodity)

        lower_bounds = [targets.get(c, Fraction(0)) - stock.get(c, Fraction(0)) for c in ordered]
        try:
            solution = minimize_covering(costs=costs, columns=columns, lower_bounds=lower_bounds)
        except InfeasibleLinearProgram as exc:
            message = "targets cannot be supplied from configured raw items and routes"
            raise NoRouteError(message) from exc

        route_values = solution.variables[: len(routes)]
        raw_values = solution.variables[len(routes) :]
        steps = tuple(
            PlanStep(route.name, route.worker_kind, amount)
            for route, amount in zip(routes, route_values, strict=True)
            if amount
        )
        raw_required = {
            commodity: amount
            for commodity, amount in zip(raw_variables, raw_values, strict=True)
            if amount
        }

        final_balance = {commodity: stock.get(commodity, Fraction(0)) for commodity in ordered}
        for route, runs in zip(routes, route_values, strict=True):
            if not runs:
                continue
            for commodity, amount in route.outputs.items():
                _add(final_balance, commodity, runs * Fraction(amount))
            for commodity, amount in route.inputs.items():
                _add(final_balance, commodity, -runs * Fraction(amount))
        for commodity, amount in raw_required.items():
            _add(final_balance, commodity, amount)

        surplus = {
            commodity: balance - targets.get(commodity, Fraction(0))
            for commodity, balance in final_balance.items()
            if balance > targets.get(commodity, Fraction(0))
        }
        return MaterialPlan(raw_required=raw_required, steps=steps, expected_surplus=surplus)


def _nonnegative(values: Mapping[Commodity, Amount]) -> dict[Commodity, Amount]:
    result: dict[Commodity, Amount] = {}
    for commodity, amount in values.items():
        value = Fraction(amount)
        if value < 0:
            raise ValueError(f"negative amount for {commodity}")
        if value:
            result[commodity] = value
    return result


def _add(values: dict[object, Amount], key: object, delta: Amount) -> None:
    new_value = values.get(key, Fraction(0)) + delta
    if new_value:
        values[key] = new_value
    else:
        values.pop(key, None)
