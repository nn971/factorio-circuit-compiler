"""Receding-horizon controller oracle for the first autonomous-mall quality prototype.

This module is deliberately still Python/offline logic.  It closes the loop around the
quality-policy LP so we can validate controller semantics with fake dispatch/workers
before encoding the policy as circuit microcode.

The important separation is:

* ``solve_quality_policy`` may import Normal-quality raw material as an *economic*
  variable when measuring the optimum;
* the runtime controller dispatches only actions whose same-quality ingredients are
  physically present in the supplied stock snapshot;
* only one stochastic quality action is in flight in this first vertical slice, so a
  real completion is observed before replanning.
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
    """Outcome of one controller decision."""

    DISPATCH = "dispatch"
    SATISFIED = "satisfied"
    BUSY = "busy"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class QualityDispatchIntent:
    """One physical action requested by the controller.

    ``count`` is one for the first stochastic prototype: after every craft/recycle we
    observe the actual result and replan.  Batching is a later dispatcher concern.
    """

    action: QualityAction
    count: int = 1

    @property
    def inputs(self) -> Mapping[Commodity, Amount]:
        return self.action.inputs


@dataclass(frozen=True)
class QualityDecision:
    """Controller result together with useful oracle diagnostics."""

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
    """Gold-standard one-action-at-a-time controller backed by the exact LP oracle.

    The LP is intentionally recomputed from the current stock snapshot.  This class is
    therefore a semantic oracle for the later compiled/circuit controller, not the
    intended final in-game implementation.

    Among actions that belong to the current optimum and are physically executable, we
    prefer:

    1. recycling a non-target-quality final product;
    2. the furthest-downstream craft in the canonical recipe DAG;
    3. the highest feasible input quality at that recipe.

    If the optimum mixes module profiles for one recipe/quality lane, a tiny weighted
    deficit scheduler realizes the LP ratio over repeated dispatches.
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
        """Choose at most one executable action from the current optimal policy."""

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
        feasible = tuple(
            step for step in positive if _is_feasible(step.action, normalized)
        )
        if not feasible:
            blocked = self._blocked_requirements(positive, normalized)
            return QualityDecision(
                QualityDecisionKind.BLOCKED,
                blocked_on=blocked,
                plan=plan,
            )

        lane_steps = self._best_lane(feasible)
        action = self._choose_profile(lane_steps)
        return QualityDecision(
            QualityDecisionKind.DISPATCH,
            intent=QualityDispatchIntent(action),
            plan=plan,
        )

    def _priority(self, action: QualityAction) -> tuple[int, int, int]:
        recycle_priority = 1 if action.kind is QualityActionKind.RECYCLE else 0
        depth = self._depth[action.recipe_name]
        return (recycle_priority, depth, int(action.base_quality))

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
            chosen = steps[0].action
        else:
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

            chosen = max(steps, key=deficit).action

        lane = (chosen.kind, chosen.recipe_name, chosen.base_quality)
        self._lane_dispatches[lane] = self._lane_dispatches.get(lane, 0) + 1
        self._action_dispatches[chosen.name] = self._action_dispatches.get(chosen.name, 0) + 1
        return chosen

    def _blocked_requirements(self, steps, stock: Mapping[Commodity, Amount]) -> dict[Commodity, Amount]:
        if not steps:
            return {}
        # When no planned action can run, explain the earliest entry-side craft that
        # would admit material into the plan.  Reporting a downstream recycle/craft
        # would merely describe a consequence of the same upstream shortage.
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
    """Single-worker fake dispatcher for controller-loop tests.

    Inputs are removed at dispatch time, representing an atomic reservation plus pickup.
    ``finish`` accepts externally chosen *actual* outputs, so tests may feed lucky or
    unlucky quality outcomes without baking another simulator into the controller.
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
