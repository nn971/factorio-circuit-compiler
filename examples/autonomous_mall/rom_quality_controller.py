"""LP-free autonomous controller driven only by the compact quality-policy ROM.

This module is the semantic reference for the eventual circuit interpreter.  It does
not consult ``CompiledTargetPolicy`` or the LP solver at runtime.  All target-specific
policy choices arrive through integer recipe records and deduplicated schedule tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from types import MappingProxyType
from typing import Mapping

from .model import Amount, Commodity, Quality
from .quality_policy_graph import QualityAction, QualityActionGraph, QualityActionKind
from .quality_policy_rom import (
    QualityPolicyRom,
    RomRecipeRecord,
    RomSchedule,
    RomTargetPolicy,
)


class RomDecisionKind(Enum):
    DISPATCH = "dispatch"
    SATISFIED = "satisfied"
    BUSY = "busy"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class RomDispatchIntent:
    demand_target: Commodity
    action: QualityAction
    count: int = 1

    @property
    def inputs(self) -> Mapping[Commodity, Amount]:
        return self.action.inputs


@dataclass(frozen=True)
class RomDecision:
    kind: RomDecisionKind
    selected_target: Commodity | None = None
    intent: RomDispatchIntent | None = None
    blocked_on: Mapping[Commodity, Amount] = MappingProxyType({})

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


class RomAutonomousQualityController:
    """Execute dynamic Legendary demand using only a compact static policy ROM."""

    def __init__(self, graph: QualityActionGraph, rom: QualityPolicyRom) -> None:
        self.graph = graph
        self.rom = rom
        self._recipe_id = {name: index for index, name in enumerate(rom.recipe_names)}
        self._profile_id = {
            (profile.productivity_modules, profile.quality_modules): index
            for index, profile in enumerate(rom.profiles)
        }
        self._craft_actions: dict[tuple[int, Quality, int], QualityAction] = {}
        self._recycle_actions: dict[tuple[int, Quality], QualityAction] = {}

        for action in graph.actions:
            try:
                recipe_id = self._recipe_id[action.recipe_name]
            except KeyError:
                continue
            if action.kind is QualityActionKind.CRAFT:
                key = (
                    action.module_profile.productivity_modules,
                    action.module_profile.quality_modules,
                )
                try:
                    profile_id = self._profile_id[key]
                except KeyError:
                    # The profile is unused by every compiled target and therefore was
                    # intentionally omitted from the ROM profile table.
                    continue
                lookup = (recipe_id, action.base_quality, profile_id)
                if lookup in self._craft_actions:
                    raise ValueError(f"duplicate ROM craft action for {lookup}")
                self._craft_actions[lookup] = action
            elif action.kind is QualityActionKind.RECYCLE:
                lookup = (recipe_id, action.base_quality)
                if lookup in self._recycle_actions:
                    raise ValueError(f"duplicate ROM recycle action for {lookup}")
                self._recycle_actions[lookup] = action

        self._target_dispatches: dict[Commodity, int] = {}
        self._schedule_dispatches: dict[tuple[Commodity, int, Quality], int] = {}
        self._profile_dispatches: dict[tuple[Commodity, int, Quality, int], int] = {}
        self._validate_rom_actions()

    def decide(
        self,
        *,
        stock: Mapping[Commodity, Amount],
        demands: Mapping[Commodity, Amount],
        busy: bool = False,
    ) -> RomDecision:
        """Select at most one executable ROM action from the current demand vector."""

        normalized_stock = _normalize_nonnegative(stock, label="stock")
        normalized_demands = _normalize_nonnegative(demands, label="demand")
        unknown = set(normalized_demands) - set(self.rom.targets)
        if unknown:
            rendered = ", ".join(
                f"{commodity.item}@{commodity.quality.name.lower()}"
                for commodity in sorted(unknown, key=lambda c: (c.item, int(c.quality)))
            )
            raise ValueError(f"demand contains targets absent from ROM: {rendered}")

        deficits = {
            target: desired - normalized_stock.get(target, Fraction(0))
            for target, desired in normalized_demands.items()
            if normalized_stock.get(target, Fraction(0)) < desired
        }
        if not deficits:
            return RomDecision(RomDecisionKind.SATISFIED)
        if busy:
            return RomDecision(RomDecisionKind.BUSY)

        blocked_total: dict[Commodity, Amount] = {}
        for target in self._ordered_targets(deficits, normalized_demands):
            policy = self.rom.target_policy(target)
            selected = self._select_action(target, policy, normalized_stock)
            if selected is not None:
                return RomDecision(
                    RomDecisionKind.DISPATCH,
                    selected_target=target,
                    intent=RomDispatchIntent(target, selected),
                )
            for commodity, amount in self._blocked_requirements(policy, normalized_stock).items():
                blocked_total[commodity] = blocked_total.get(commodity, Fraction(0)) + amount

        return RomDecision(RomDecisionKind.BLOCKED, blocked_on=blocked_total)

    def record_dispatch(self, intent: RomDispatchIntent) -> None:
        """Advance fairness/schedule phase only after a dispatcher accepts the intent."""

        if intent.count <= 0:
            raise ValueError("dispatch count must be positive")
        policy = self.rom.target_policy(intent.demand_target)
        recipe_id = self._recipe_id[intent.action.recipe_name]
        record = next((item for item in policy.records if item.recipe_id == recipe_id), None)
        if record is None:
            raise ValueError("intent recipe is outside target ROM program")

        self._target_dispatches[intent.demand_target] = (
            self._target_dispatches.get(intent.demand_target, 0) + intent.count
        )
        if intent.action.kind is QualityActionKind.RECYCLE:
            if not record.recycle_final:
                raise ValueError("intent recycles a non-final ROM record")
            return

        profile_key = (
            intent.action.module_profile.productivity_modules,
            intent.action.module_profile.quality_modules,
        )
        profile_id = self._profile_id[profile_key]
        quality = intent.action.base_quality
        schedule_id = record.schedule_for(quality)
        schedule = self.rom.schedules[schedule_id]
        if profile_id not in {choice.profile_id for choice in schedule.choices}:
            raise ValueError("intent profile is outside ROM schedule")

        lane_key = (intent.demand_target, recipe_id, quality)
        self._schedule_dispatches[lane_key] = (
            self._schedule_dispatches.get(lane_key, 0) + intent.count
        )
        profile_lane = (intent.demand_target, recipe_id, quality, profile_id)
        self._profile_dispatches[profile_lane] = (
            self._profile_dispatches.get(profile_lane, 0) + intent.count
        )

    def _ordered_targets(
        self,
        deficits: Mapping[Commodity, Amount],
        demands: Mapping[Commodity, Amount],
    ) -> tuple[Commodity, ...]:
        def key(target: Commodity):
            pressure = Fraction(deficits[target]) / Fraction(demands[target])
            policy = self.rom.target_policy(target)
            return (
                -pressure,
                self._target_dispatches.get(target, 0),
                -policy.unit_raw_cost,
                target.item,
                int(target.quality),
            )

        return tuple(sorted(deficits, key=key))

    def _select_action(
        self,
        target: Commodity,
        policy: RomTargetPolicy,
        stock: Mapping[Commodity, Amount],
    ) -> QualityAction | None:
        # A rejected final product is already the furthest-downstream material in the
        # campaign, so recycle it before committing more upstream raw material.
        for record in reversed(policy.records):
            if not record.recycle_final:
                continue
            for quality in reversed(tuple(Quality)):
                if quality is Quality.LEGENDARY:
                    continue
                action = self._recycle_actions.get((record.recipe_id, quality))
                if action is not None and _inputs_available(action, stock):
                    return action

        # Otherwise consume the furthest-downstream, highest-quality feasible stock.
        for record in reversed(policy.records):
            for quality in reversed(tuple(Quality)):
                action = self._choose_craft_action(target, record, quality)
                if _inputs_available(action, stock):
                    return action
        return None

    def _choose_craft_action(
        self,
        target: Commodity,
        record: RomRecipeRecord,
        quality: Quality,
    ) -> QualityAction:
        schedule = self.rom.schedules[record.schedule_for(quality)]
        profile_id = self._choose_profile(target, record.recipe_id, quality, schedule)
        try:
            return self._craft_actions[(record.recipe_id, quality, profile_id)]
        except KeyError as exc:
            raise ValueError(
                "ROM schedule selects unavailable craft profile for "
                f"{self.rom.recipe_name(record.recipe_id)}@{quality.name.lower()}"
            ) from exc

    def _choose_profile(
        self,
        target: Commodity,
        recipe_id: int,
        quality: Quality,
        schedule: RomSchedule,
    ) -> int:
        if len(schedule.choices) == 1:
            return schedule.choices[0].profile_id

        lane = (target, recipe_id, quality)
        dispatched = self._schedule_dispatches.get(lane, 0)

        def deficit(choice):
            served = self._profile_dispatches.get(
                (target, recipe_id, quality, choice.profile_id), 0
            )
            desired_after_next = Fraction(dispatched + 1) * choice.weight
            return (desired_after_next - served, -choice.profile_id)

        return max(schedule.choices, key=deficit).profile_id

    def _blocked_requirements(
        self,
        policy: RomTargetPolicy,
        stock: Mapping[Commodity, Amount],
    ) -> dict[Commodity, Amount]:
        # The first record is the earliest producer in canonical topological order.
        record = policy.records[0]
        action = self._choose_craft_action(policy.target, record, Quality.NORMAL)
        return {
            commodity: Fraction(required) - stock.get(commodity, Fraction(0))
            for commodity, required in action.inputs.items()
            if stock.get(commodity, Fraction(0)) < Fraction(required)
        }

    def _validate_rom_actions(self) -> None:
        for target, policy in self.rom.targets.items():
            for record in policy.records:
                for quality in Quality:
                    schedule = self.rom.schedules[record.schedule_for(quality)]
                    for choice in schedule.choices:
                        if (record.recipe_id, quality, choice.profile_id) not in self._craft_actions:
                            raise ValueError(
                                "ROM references absent craft action: "
                                f"target={target.item}, "
                                f"recipe={self.rom.recipe_name(record.recipe_id)}, "
                                f"quality={quality.name.lower()}, profile={choice.profile_id}"
                            )
                if record.recycle_final:
                    for quality in Quality:
                        if quality is Quality.LEGENDARY:
                            continue
                        if (record.recipe_id, quality) not in self._recycle_actions:
                            raise ValueError(
                                "ROM final record lacks recycler action: "
                                f"{self.rom.recipe_name(record.recipe_id)}@{quality.name.lower()}"
                            )


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
