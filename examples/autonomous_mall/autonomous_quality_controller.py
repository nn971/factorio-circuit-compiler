"""LP-free runtime controller for a variable-demand autonomous quality mall.

All expensive optimization is compiled into ``CompiledQualityPolicyBook``.  Runtime
logic sees only live desired stock, live physical stock and the static policy ROM.  It
chooses at most one stochastic action, waits for the actual completion, then evaluates
the *new* demand vector again.

This first controller supports dynamically varying amounts for configured Legendary
mall products.  Several products may be demanded simultaneously.  The arbitration rule
uses relative shortage and fair service counts, and a blocked target never prevents
another target with executable work from making progress.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from fractions import Fraction
from types import MappingProxyType
from typing import Mapping

from .compiled_quality_policy import (
    CompiledQualityPolicyBook,
    CompiledTargetPolicy,
    PolicyLane,
    WeightedPolicyAction,
)
from .model import Amount, Commodity
from .quality_policy_graph import QualityAction, QualityActionGraph, QualityActionKind


class AutonomousDecisionKind(Enum):
    DISPATCH = "dispatch"
    SATISFIED = "satisfied"
    BUSY = "busy"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class AutonomousDispatchIntent:
    """One accepted-policy action proposed for a currently deficient mall target."""

    demand_target: Commodity
    action: QualityAction
    count: int = 1

    @property
    def inputs(self) -> Mapping[Commodity, Amount]:
        return self.action.inputs


@dataclass(frozen=True)
class AutonomousDecision:
    kind: AutonomousDecisionKind
    selected_target: Commodity | None = None
    intent: AutonomousDispatchIntent | None = None
    blocked_on: Mapping[Commodity, Amount] = field(default_factory=dict)

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


class AutonomousQualityController:
    """Demand-responsive controller consuming only a compiled policy book.

    ``decide`` is pure with respect to physical stock and demand: policy cadence/fairness
    state advances only through ``record_dispatch`` after a dispatcher accepts the
    intent.  Therefore a transiently busy worker pool cannot distort arbitration.
    """

    def __init__(
        self,
        graph: QualityActionGraph,
        policy_book: CompiledQualityPolicyBook,
    ) -> None:
        self.graph = graph
        self.policy_book = policy_book
        self._actions = {action.name: action for action in graph.actions}
        self._depth = {
            recipe.name: index
            for index, recipe in enumerate(graph.recipe_dag.recipes)
        }
        for policy in policy_book.policies.values():
            for weighted_actions in policy.lane_actions.values():
                for weighted in weighted_actions:
                    if weighted.action_name not in self._actions:
                        raise ValueError(
                            f"policy action is absent from graph: {weighted.action_name}"
                        )

        self._target_dispatches: dict[Commodity, int] = {}
        self._lane_dispatches: dict[tuple[Commodity, PolicyLane], int] = {}
        self._action_dispatches: dict[tuple[Commodity, str], int] = {}

    def decide(
        self,
        *,
        stock: Mapping[Commodity, Amount],
        demands: Mapping[Commodity, Amount],
        busy: bool = False,
    ) -> AutonomousDecision:
        """Choose at most one executable action from the current live demand vector."""

        normalized_stock = _normalize_nonnegative(stock, label="stock")
        normalized_demands = _normalize_nonnegative(demands, label="demand")
        unknown = set(normalized_demands) - self.policy_book.targets
        if unknown:
            rendered = ", ".join(
                f"{commodity.item}@{commodity.quality.name.lower()}"
                for commodity in sorted(
                    unknown, key=lambda c: (c.item, int(c.quality))
                )
            )
            raise ValueError(f"demand contains uncompiled mall targets: {rendered}")

        active = {
            target: desired - normalized_stock.get(target, Fraction(0))
            for target, desired in normalized_demands.items()
            if normalized_stock.get(target, Fraction(0)) < desired
        }
        if not active:
            return AutonomousDecision(AutonomousDecisionKind.SATISFIED)
        if busy:
            return AutonomousDecision(AutonomousDecisionKind.BUSY)

        ordered_targets = self._ordered_targets(
            active,
            demands=normalized_demands,
        )
        blocked_total: dict[Commodity, Amount] = {}
        for target in ordered_targets:
            policy = self.policy_book.policy_for(target)
            lane = self._best_feasible_lane(policy, normalized_stock)
            if lane is not None:
                action = self._choose_lane_action(target, lane, policy)
                return AutonomousDecision(
                    AutonomousDecisionKind.DISPATCH,
                    selected_target=target,
                    intent=AutonomousDispatchIntent(target, action),
                )

            for commodity, amount in self._blocked_requirements(
                policy, normalized_stock
            ).items():
                blocked_total[commodity] = (
                    blocked_total.get(commodity, Fraction(0)) + amount
                )

        return AutonomousDecision(
            AutonomousDecisionKind.BLOCKED,
            blocked_on=blocked_total,
        )

    def record_dispatch(self, intent: AutonomousDispatchIntent) -> None:
        """Advance target fairness and profile-mixture phases after acceptance."""

        if intent.count <= 0:
            raise ValueError("dispatch count must be positive")
        target = intent.demand_target
        policy = self.policy_book.policy_for(target)
        lane = PolicyLane(
            intent.action.kind,
            intent.action.recipe_name,
            intent.action.base_quality,
        )
        if lane not in policy.lane_actions:
            raise ValueError("intent action lane is not part of target policy")
        allowed = {
            weighted.action_name for weighted in policy.lane_actions[lane]
        }
        if intent.action.name not in allowed:
            raise ValueError("intent action is not part of target lane policy")

        self._target_dispatches[target] = (
            self._target_dispatches.get(target, 0) + intent.count
        )
        lane_key = (target, lane)
        self._lane_dispatches[lane_key] = (
            self._lane_dispatches.get(lane_key, 0) + intent.count
        )
        action_key = (target, intent.action.name)
        self._action_dispatches[action_key] = (
            self._action_dispatches.get(action_key, 0) + intent.count
        )

    def _ordered_targets(
        self,
        deficits: Mapping[Commodity, Amount],
        *,
        demands: Mapping[Commodity, Amount],
    ) -> tuple[Commodity, ...]:
        """Prioritize relative shortage, then targets that received less service.

        Relative shortage makes a newly empty target react immediately even when another
        target has a much larger configured stock level.  The dispatch-count tie breaker
        round-robins targets that are similarly short instead of running one complete
        stochastic campaign before touching the others.
        """

        def key(target: Commodity):
            desired = demands[target]
            pressure = Fraction(deficits[target]) / Fraction(desired)
            policy = self.policy_book.policy_for(target)
            return (
                -pressure,
                self._target_dispatches.get(target, 0),
                -policy.unit_raw_cost,
                target.item,
                int(target.quality),
            )

        return tuple(sorted(deficits, key=key))

    def _best_feasible_lane(
        self,
        policy: CompiledTargetPolicy,
        stock: Mapping[Commodity, Amount],
    ) -> PolicyLane | None:
        feasible = [
            lane
            for lane, weighted_actions in policy.lane_actions.items()
            if weighted_actions
            and _inputs_available(
                self._actions[weighted_actions[0].action_name], stock
            )
        ]
        if not feasible:
            return None
        return max(feasible, key=self._lane_priority)

    def _lane_priority(self, lane: PolicyLane) -> tuple[int, int, int]:
        recycle = 1 if lane.kind is QualityActionKind.RECYCLE else 0
        return (recycle, self._depth[lane.recipe_name], int(lane.base_quality))

    def _choose_lane_action(
        self,
        target: Commodity,
        lane: PolicyLane,
        policy: CompiledTargetPolicy,
    ) -> QualityAction:
        weighted = policy.lane_actions[lane]
        if len(weighted) == 1:
            return self._actions[weighted[0].action_name]

        lane_key = (target, lane)
        dispatched = self._lane_dispatches.get(lane_key, 0)

        def deficit(candidate: WeightedPolicyAction) -> tuple[Fraction, str]:
            served = self._action_dispatches.get(
                (target, candidate.action_name), 0
            )
            desired_after_next = Fraction(dispatched + 1) * candidate.weight
            return (desired_after_next - served, candidate.action_name)

        chosen = max(weighted, key=deficit)
        return self._actions[chosen.action_name]

    def _blocked_requirements(
        self,
        policy: CompiledTargetPolicy,
        stock: Mapping[Commodity, Amount],
    ) -> dict[Commodity, Amount]:
        craft_lanes = [
            lane
            for lane in policy.lane_actions
            if lane.kind is QualityActionKind.CRAFT
        ]
        if not craft_lanes:
            return {}
        entry = min(craft_lanes, key=self._lane_priority)
        action = self._actions[policy.lane_actions[entry][0].action_name]
        return {
            commodity: Fraction(required) - stock.get(commodity, Fraction(0))
            for commodity, required in action.inputs.items()
            if stock.get(commodity, Fraction(0)) < Fraction(required)
        }


def _normalize_nonnegative(
    values: Mapping[Commodity, Amount],
    *,
    label: str,
) -> dict[Commodity, Amount]:
    result: dict[Commodity, Amount] = {}
    for commodity, amount in values.items():
        value = Fraction(amount)
        if value < 0:
            raise ValueError(f"negative {label} for {commodity}")
        if value:
            result[commodity] = value
    return result


def _inputs_available(
    action: QualityAction,
    stock: Mapping[Commodity, Amount],
) -> bool:
    return all(
        stock.get(commodity, Fraction(0)) >= Fraction(amount)
        for commodity, amount in action.inputs.items()
    )
