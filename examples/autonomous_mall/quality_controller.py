"""Receding-horizon controller oracle for the first quality-mall prototype.

The LP remains an offline/economic oracle. Runtime dispatch only uses physically
present same-quality ingredients, and the first vertical slice keeps one stochastic
action in flight so actual outputs are observed before replanning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from fractions import Fraction
from types import MappingProxyType
from typing import Mapping

from .model import Amount, Commodity, Quality
from .quality_policy import QualityPlan, solve_quality_policy
from .quality_policy_graph import QualityAction, QualityActionGraph, QualityActionKind


class QualityDecisionKind(Enum):
    DISPATCH = "dispatch"
    SATISFIED = "satisfied"
    BUSY = "busy"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class QualityDispatchIntent:
    action: QualityAction
    count: int = 1

    @property
    def inputs(self) -> Mapping[Commodity, Amount]:
        return self.action.inputs


@dataclass(frozen=True)
class QualityDecision:
    kind: QualityDecisionKind
    intent: QualityDispatchIntent | None = None
    blocked_on: Mapping[Commodity, Amount] = field(default_factory=dict)
    plan: QualityPlan | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "blocked_on",
            MappingProxyType(
                {
                    commodity: Fraction(amount)
                    for commodity, amount in self.blocked_on.items()
                    if Fraction(amount) > 0
                }
            ),
        )


class RecedingHorizonQualityController:
    """Gold-standard one-action controller backed by the exact expected-flow LP.

    This is a semantic oracle for the later circuit controller. Among optimal actions
    that are executable from current stock it prefers final-product recycling, then the
    furthest-downstream craft, then the highest input quality. Fractional module-profile
    mixtures use a weighted-deficit phase that advances only after dispatch acceptance.
    """

    def __init__(
        self,
        graph: QualityActionGraph,
        *,
        target_item: str,
        target_quality: Quality = Quality.LEGENDARY,
        target_amount: Amount = Fraction(1),
        raw_costs: Mapping[str, Amount] | None = None,
    ) -> None:
        target = Commodity(target_item, target_quality)
        if target not in set(graph.commodities):
            raise ValueError(f"target {target_item!r}@{target_quality.name.lower()} is outside graph")
        amount = Fraction(target_amount)
        if amount <= 0:
            raise ValueError("target_amount must be positive")
        self.graph = graph
        self.target = target
        self.target_amount = amount
        self.raw_costs = dict(raw_costs) if raw_costs is not None else None
        self._depth = {
            recipe.name: index
            for index, recipe in enumerate(graph.recipe_dag.recipes)
        }
        self._lane_dispatches: dict[tuple[QualityActionKind, str, Quality], int] = {}
        self._action_dispatches: dict[str, int] = {}

    def decide(
        self,
        stock: Mapping[Commodity, Amount],
        *,
        busy: bool = False,
    ) -> QualityDecision:
        normalized = _normalize_stock(stock)
        if normalized.get(self.target, Fraction(0)) >= self.target_amount:
            return QualityDecision(QualityDecisionKind.SATISFIED)
        if busy:
            return QualityDecision(QualityDecisionKind.BUSY)

        plan = solve_quality_policy(
            self.graph,
            targets={self.target: self.target_amount},
            stock=normalized,
            raw_costs=self.raw_costs,
        )
        positive = tuple(step for step in plan.steps if step.expected_runs > 0)
        feasible = tuple(step for step in positive if _is_feasible(step.action, normalized))
        if not feasible:
            return QualityDecision(
                QualityDecisionKind.BLOCKED,
                blocked_on=self._blocked_requirements(positive, normalized),
                plan=plan,
            )

        lane_steps = self._best_lane(feasible)
        action = self._choose_profile(lane_steps)
        return QualityDecision(
            QualityDecisionKind.DISPATCH,
            intent=QualityDispatchIntent(action),
            plan=plan,
        )

    def record_dispatch(self, intent: QualityDispatchIntent) -> None:
        """Advance mixture phase after a dispatcher has accepted ``intent``."""

        action = intent.action
        lane = (action.kind, action.recipe_name, action.base_quality)
        self._lane_dispatches[lane] = self._lane_dispatches.get(lane, 0) + intent.count
        self._action_dispatches[action.name] = self._action_dispatches.get(action.name, 0) + intent.count

    def _priority(self, action: QualityAction) -> tuple[int, int, int]:
        recycle_priority = 1 if action.kind is QualityActionKind.RECYCLE else 0
        return (recycle_priority, self._depth[action.recipe_name], int(action.base_quality))

    def _best_lane(self, steps):
        best_priority = max(self._priority(step.action) for step in steps)
        best = tuple(step for step in steps if self._priority(step.action) == best_priority)
        first = best[0].action
        lane = (first.kind, first.recipe_name, first.base_quality)
        return tuple(
            step
            for step in best
            if (step.action.kind, step.action.recipe_name, step.action.base_quality) == lane
        )

    def _choose_profile(self, steps) -> QualityAction:
        if len(steps) == 1:
            return steps[0].action

        lane_action = steps[0].action
        lane = (lane_action.kind, lane_action.recipe_name, lane_action.base_quality)
        dispatched = self._lane_dispatches.get(lane, 0)
        total_weight = sum(
            (Fraction(step.expected_runs) for step in steps),
            start=Fraction(0),
        )
        if total_weight <= 0:
            raise RuntimeError("positive policy lane has zero total weight")

        def deficit(step) -> tuple[Fraction, str]:
            share = Fraction(step.expected_runs) / total_weight
            served = self._action_dispatches.get(step.action.name, 0)
            desired_after_next = Fraction(dispatched + 1) * share
            return (desired_after_next - served, step.action.name)

        return max(steps, key=deficit).action

    def _blocked_requirements(
        self,
        steps,
        stock: Mapping[Commodity, Amount],
    ) -> dict[Commodity, Amount]:
        if not steps:
            return {}
        # If no planned action can run, expose the earliest entry-side shortage rather
        # than a downstream action whose ingredients depend on the same shortage.
        candidate = min(steps, key=lambda step: self._priority(step.action)).action
        return {
            commodity: Fraction(required) - stock.get(commodity, Fraction(0))
            for commodity, required in candidate.inputs.items()
            if stock.get(commodity, Fraction(0)) < Fraction(required)
        }


def _normalize_stock(stock: Mapping[Commodity, Amount]) -> dict[Commodity, Amount]:
    result: dict[Commodity, Amount] = {}
    for commodity, amount in stock.items():
        value = Fraction(amount)
        if value < 0:
            raise ValueError(f"negative stock for {commodity}")
        if value:
            result[commodity] = value
    return result


def _is_feasible(action: QualityAction, stock: Mapping[Commodity, Amount]) -> bool:
    return all(
        stock.get(commodity, Fraction(0)) >= Fraction(amount)
        for commodity, amount in action.inputs.items()
    )


@dataclass
class FakeQualityDispatcher:
    """Single-worker fake dispatcher used to close the first controller loop.

    Inputs are removed at dispatch, representing atomic reservation/pickup. ``finish``
    accepts caller-selected actual outputs so tests can inject lucky/unlucky quality
    outcomes without coupling the controller to another simulator.
    """

    stock: dict[Commodity, Amount]
    active: QualityDispatchIntent | None = None
    _reserved_inputs: dict[Commodity, Amount] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.stock = _normalize_stock(self.stock)

    @property
    def busy(self) -> bool:
        return self.active is not None

    def dispatch(self, intent: QualityDispatchIntent) -> None:
        if self.active is not None:
            raise RuntimeError("fake worker is already busy")
        if intent.count != 1:
            raise ValueError("first fake dispatcher supports count=1 only")
        required = {commodity: Fraction(amount) for commodity, amount in intent.inputs.items()}
        missing = {
            commodity: amount - self.stock.get(commodity, Fraction(0))
            for commodity, amount in required.items()
            if self.stock.get(commodity, Fraction(0)) < amount
        }
        if missing:
            details = ", ".join(
                f"{commodity.item}@{commodity.quality.name.lower()}={amount}"
                for commodity, amount in sorted(
                    missing.items(), key=lambda pair: (pair[0].item, int(pair[0].quality))
                )
            )
            raise RuntimeError(f"fake dispatcher lacks inputs: {details}")
        for commodity, amount in required.items():
            remaining = self.stock.get(commodity, Fraction(0)) - amount
            if remaining:
                self.stock[commodity] = remaining
            else:
                self.stock.pop(commodity, None)
        self._reserved_inputs = required
        self.active = intent

    def finish(self, outputs: Mapping[Commodity, Amount]) -> QualityDispatchIntent:
        if self.active is None:
            raise RuntimeError("fake worker is idle")
        intent = self.active
        for commodity, amount in outputs.items():
            value = Fraction(amount)
            if value < 0:
                raise ValueError("fake worker outputs must be non-negative")
            if value:
                self.stock[commodity] = self.stock.get(commodity, Fraction(0)) + value
        self.active = None
        self._reserved_inputs = {}
        return intent

    def cancel(self) -> QualityDispatchIntent:
        if self.active is None:
            raise RuntimeError("fake worker is idle")
        intent = self.active
        for commodity, amount in self._reserved_inputs.items():
            self.stock[commodity] = self.stock.get(commodity, Fraction(0)) + amount
        self.active = None
        self._reserved_inputs = {}
        return intent
