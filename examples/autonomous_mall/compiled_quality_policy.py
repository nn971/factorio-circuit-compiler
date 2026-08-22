"""Compile target-specific quality policies for a demand-flexible mall controller.

The exact LP remains an offline oracle.  This module turns that oracle into a static
policy book that can be consumed by a runtime controller without re-solving an LP.
Each configured mall target gets:

* the zero-stock optimal module-profile mixture for lanes used in steady production;
* an offline-computed fallback profile for otherwise-unused quality lanes, so newly
  observed high-quality stock can enter the policy immediately;
* only recipe ancestry relevant to that target.

The first prototype compiles Legendary final-product policies.  Demand *amounts* are
fully dynamic at runtime; extending the book to other target qualities is deliberately
kept as a later step because exact lower-quality demand makes unused quality-module
slots economically relevant.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from types import MappingProxyType
from typing import Iterable, Mapping

from .model import Amount, Commodity, Quality
from .quality_policy import solve_quality_policy
from .quality_policy_graph import (
    QualityAction,
    QualityActionGraph,
    QualityActionKind,
)


@dataclass(frozen=True, order=True)
class PolicyLane:
    """One target-independent physical recipe/quality lane."""

    kind: QualityActionKind
    recipe_name: str
    base_quality: Quality


@dataclass(frozen=True)
class WeightedPolicyAction:
    """One action and its normalized long-run weight inside a policy lane."""

    action_name: str
    weight: Amount

    def __post_init__(self) -> None:
        weight = Fraction(self.weight)
        if weight <= 0:
            raise ValueError("policy action weight must be positive")
        object.__setattr__(self, "weight", weight)


@dataclass(frozen=True)
class CompiledTargetPolicy:
    """Static policy for one exact final target commodity."""

    target: Commodity
    unit_raw_cost: Amount
    recipe_names: frozenset[str]
    lane_actions: Mapping[PolicyLane, tuple[WeightedPolicyAction, ...]]

    def __post_init__(self) -> None:
        raw_cost = Fraction(self.unit_raw_cost)
        if raw_cost < 0:
            raise ValueError("unit_raw_cost must be non-negative")
        normalized: dict[PolicyLane, tuple[WeightedPolicyAction, ...]] = {}
        for lane, actions in self.lane_actions.items():
            if not actions:
                raise ValueError(f"policy lane has no actions: {lane}")
            total = sum((action.weight for action in actions), start=Fraction(0))
            normalized[lane] = tuple(
                WeightedPolicyAction(action.action_name, action.weight / total)
                for action in actions
            )
        object.__setattr__(self, "unit_raw_cost", raw_cost)
        object.__setattr__(self, "lane_actions", MappingProxyType(normalized))


@dataclass(frozen=True)
class CompiledQualityPolicyBook:
    """Offline policy ROM for every configured mall target."""

    policies: Mapping[Commodity, CompiledTargetPolicy]

    def __post_init__(self) -> None:
        resolved = dict(self.policies)
        if not resolved:
            raise ValueError("compiled policy book must contain at least one target")
        object.__setattr__(self, "policies", MappingProxyType(resolved))

    @property
    def targets(self) -> frozenset[Commodity]:
        return frozenset(self.policies)

    def policy_for(self, target: Commodity) -> CompiledTargetPolicy:
        try:
            return self.policies[target]
        except KeyError as exc:
            raise KeyError(
                f"target {target.item}@{target.quality.name.lower()} is not compiled"
            ) from exc

    def to_json_dict(self) -> dict[str, object]:
        def lane_key(lane: PolicyLane) -> str:
            return (
                f"{lane.kind.value}:{lane.recipe_name}:"
                f"{lane.base_quality.name.lower()}"
            )

        return {
            "targets": {
                f"{target.item}@{target.quality.name.lower()}": {
                    "unit_raw_cost": str(policy.unit_raw_cost),
                    "recipes": sorted(policy.recipe_names),
                    "lanes": {
                        lane_key(lane): [
                            {
                                "action": weighted.action_name,
                                "weight": str(weighted.weight),
                            }
                            for weighted in actions
                        ]
                        for lane, actions in sorted(
                            policy.lane_actions.items(),
                            key=lambda pair: (
                                pair[0].kind.value,
                                pair[0].recipe_name,
                                int(pair[0].base_quality),
                            ),
                        )
                    },
                }
                for target, policy in sorted(
                    self.policies.items(),
                    key=lambda pair: (pair[0].item, int(pair[0].quality)),
                )
            }
        }


def compile_quality_policy_book(
    graph: QualityActionGraph,
    *,
    target_items: Iterable[str] | None = None,
    raw_costs: Mapping[str, Amount] | None = None,
) -> CompiledQualityPolicyBook:
    """Compile Legendary policies for a configured set of mall target roots.

    The recipe DAG may contain several roots.  ``target_items`` selects which of those
    roots become autonomously demandable at runtime; by default every DAG target is
    compiled.  All LP calls happen here, offline.
    """

    roots = tuple(dict.fromkeys(target_items or graph.recipe_dag.targets))
    if not roots:
        raise ValueError("at least one mall target is required")
    unknown = set(roots) - set(graph.recipe_dag.targets)
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(
            "compiled target items must be roots of the recipe DAG; unknown roots: "
            + names
        )

    actions_by_name = {action.name: action for action in graph.actions}
    policies: dict[Commodity, CompiledTargetPolicy] = {}
    for item in roots:
        target = Commodity(item, Quality.LEGENDARY)
        target_recipe = graph.recipe_dag.recipe_for(item)
        if target_recipe is None:
            raise ValueError(f"mall target {item!r} lies on the raw boundary")
        ancestry = _recipe_ancestry(graph, item)
        baseline = solve_quality_policy(
            graph,
            targets={target: Fraction(1)},
            stock={},
            raw_costs=raw_costs,
        )
        active_by_lane: dict[PolicyLane, list[tuple[QualityAction, Fraction]]] = {}
        for step in baseline.steps:
            action = step.action
            if not _action_relevant_to_target(
                action,
                final_recipe_name=target_recipe.name,
                ancestry=ancestry,
            ):
                continue
            lane = _lane_of(action)
            active_by_lane.setdefault(lane, []).append(
                (action, Fraction(step.expected_runs))
            )

        lane_actions: dict[PolicyLane, tuple[WeightedPolicyAction, ...]] = {}
        candidates_by_lane: dict[PolicyLane, list[QualityAction]] = {}
        for action in graph.actions:
            if not _action_relevant_to_target(
                action,
                final_recipe_name=target_recipe.name,
                ancestry=ancestry,
            ):
                continue
            candidates_by_lane.setdefault(_lane_of(action), []).append(action)

        for lane, candidates in candidates_by_lane.items():
            active = [pair for pair in active_by_lane.get(lane, ()) if pair[1] > 0]
            if active:
                lane_actions[lane] = tuple(
                    WeightedPolicyAction(action.name, runs)
                    for action, runs in sorted(active, key=lambda pair: pair[0].name)
                )
                continue

            # This lane is absent from the zero-stock optimum, but may become valuable
            # when live stock appears there.  Inputs are identical among candidates in
            # one lane, so compare only the expected continuation cost of their output.
            best_cost: Fraction | None = None
            best_action: QualityAction | None = None
            for action in sorted(candidates, key=lambda candidate: candidate.name):
                continuation = solve_quality_policy(
                    graph,
                    targets={target: Fraction(1)},
                    stock=action.outputs,
                    raw_costs=raw_costs,
                ).raw_total
                cost = Fraction(continuation)
                if best_cost is None or cost < best_cost:
                    best_cost = cost
                    best_action = action
            if best_action is None:
                raise RuntimeError(f"no fallback action for lane {lane}")
            lane_actions[lane] = (WeightedPolicyAction(best_action.name, Fraction(1)),)

        for weighted_actions in lane_actions.values():
            for weighted in weighted_actions:
                if weighted.action_name not in actions_by_name:
                    raise RuntimeError(
                        f"compiled action missing from graph: {weighted.action_name}"
                    )

        policies[target] = CompiledTargetPolicy(
            target=target,
            unit_raw_cost=baseline.raw_total,
            recipe_names=frozenset(ancestry),
            lane_actions=lane_actions,
        )

    return CompiledQualityPolicyBook(policies)


def _recipe_ancestry(graph: QualityActionGraph, target_item: str) -> frozenset[str]:
    result: set[str] = set()

    def visit(item: str) -> None:
        recipe = graph.recipe_dag.recipe_for(item)
        if recipe is None or recipe.name in result:
            return
        result.add(recipe.name)
        for ingredient in recipe.ingredients:
            visit(ingredient)

    visit(target_item)
    return frozenset(result)


def _action_relevant_to_target(
    action: QualityAction,
    *,
    final_recipe_name: str,
    ancestry: frozenset[str],
) -> bool:
    if action.recipe_name not in ancestry:
        return False
    if action.kind is QualityActionKind.CRAFT:
        return True
    # One target policy never consumes another target's reject merely because they
    # share an intermediate.  Cross-target recycling/allocation belongs to the future
    # global allocator.
    return action.recipe_name == final_recipe_name


def _lane_of(action: QualityAction) -> PolicyLane:
    return PolicyLane(action.kind, action.recipe_name, action.base_quality)
