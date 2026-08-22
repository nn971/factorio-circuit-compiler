"""Compact target-policy ROM for the variable-demand autonomous quality mall.

This is the bridge between the rich offline policy book and an eventual Factorio
circuit implementation.  Runtime policy data is reduced to integer recipe records:

* one global recipe id per canonical recipe;
* one deduplicated craft schedule id for each of the five input-quality lanes;
* one final-product recycle flag.

Each recipe record packs into two signed 32-bit Factorio signal counts.  The first word
holds a 16-bit recipe id plus flags; the second word holds five 6-bit schedule ids.
The intentionally generous 16-bit recipe namespace and 6-bit schedule namespace keep
this usable for large modpacks while leaving the physical encoding simple.

A schedule describes only *physical module profiles* and their normalized long-run
weights.  Recipe-specific productivity caps remain in the already-compiled action
graph; the runtime maps the selected profile back to the exact recipe action.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from types import MappingProxyType
from typing import Mapping

from .compiled_quality_policy import (
    CompiledQualityPolicyBook,
    PolicyLane,
    WeightedPolicyAction,
)
from .model import Amount, Commodity, Quality
from .quality_policy_graph import (
    ModuleProfile,
    QualityActionGraph,
    QualityActionKind,
)

_RECIPE_BITS = 16
_SCHEDULE_BITS = 6
_SCHEDULE_MASK = (1 << _SCHEDULE_BITS) - 1
_RECIPE_MASK = (1 << _RECIPE_BITS) - 1
_RECYCLE_FLAG = 1 << 16
_U32_MASK = (1 << 32) - 1
_I32_SIGN = 1 << 31
_I32_MOD = 1 << 32
_MAX_RECIPES = 1 << _RECIPE_BITS
_MAX_SCHEDULES = 1 << _SCHEDULE_BITS


class QualityPolicyRomError(ValueError):
    """The compiled policy cannot be represented by the first ROM ABI."""


@dataclass(frozen=True, order=True)
class RomModuleProfile:
    """One physical fixed-module worker profile."""

    productivity_modules: int
    quality_modules: int

    @property
    def name(self) -> str:
        return f"{self.productivity_modules}p{self.quality_modules}q"


@dataclass(frozen=True, order=True)
class RomScheduleChoice:
    profile_id: int
    weight: Amount

    def __post_init__(self) -> None:
        if self.profile_id < 0:
            raise ValueError("profile_id must be non-negative")
        weight = Fraction(self.weight)
        if weight <= 0:
            raise ValueError("schedule weight must be positive")
        object.__setattr__(self, "weight", weight)


@dataclass(frozen=True)
class RomSchedule:
    """Normalized long-run mixture of physical module profiles."""

    choices: tuple[RomScheduleChoice, ...]

    def __post_init__(self) -> None:
        if not self.choices:
            raise ValueError("ROM schedule must contain at least one choice")
        total = sum((choice.weight for choice in self.choices), start=Fraction(0))
        if total <= 0:
            raise ValueError("ROM schedule has zero total weight")
        normalized = tuple(
            RomScheduleChoice(choice.profile_id, choice.weight / total)
            for choice in sorted(self.choices, key=lambda choice: choice.profile_id)
        )
        object.__setattr__(self, "choices", normalized)


@dataclass(frozen=True)
class RomRecipeRecord:
    """Target-local policy for one canonical recipe across all five qualities."""

    recipe_id: int
    schedule_ids: tuple[int, int, int, int, int]
    recycle_final: bool = False

    def __post_init__(self) -> None:
        if not 0 <= self.recipe_id < _MAX_RECIPES:
            raise QualityPolicyRomError(
                f"recipe id {self.recipe_id} exceeds {_RECIPE_BITS}-bit ROM field"
            )
        if len(self.schedule_ids) != len(Quality):
            raise ValueError("recipe record requires one schedule id per quality tier")
        for schedule_id in self.schedule_ids:
            if not 0 <= schedule_id < _MAX_SCHEDULES:
                raise QualityPolicyRomError(
                    f"schedule id {schedule_id} exceeds {_SCHEDULE_BITS}-bit ROM field"
                )

    def schedule_for(self, quality: Quality) -> int:
        return self.schedule_ids[int(quality)]

    def pack(self) -> tuple[int, int]:
        """Return the two Factorio-compatible signed 32-bit signal counts."""

        descriptor = self.recipe_id | (_RECYCLE_FLAG if self.recycle_final else 0)
        schedules = 0
        for quality in Quality:
            schedules |= self.schedule_for(quality) << (_SCHEDULE_BITS * int(quality))
        return _to_i32(descriptor), _to_i32(schedules)

    @classmethod
    def unpack(cls, descriptor: int, schedules: int) -> "RomRecipeRecord":
        descriptor_u = _to_u32(descriptor)
        schedules_u = _to_u32(schedules)
        recipe_id = descriptor_u & _RECIPE_MASK
        schedule_ids = tuple(
            (schedules_u >> (_SCHEDULE_BITS * int(quality))) & _SCHEDULE_MASK
            for quality in Quality
        )
        return cls(
            recipe_id=recipe_id,
            schedule_ids=schedule_ids,  # type: ignore[arg-type]
            recycle_final=bool(descriptor_u & _RECYCLE_FLAG),
        )


@dataclass(frozen=True)
class RomTargetPolicy:
    """Compact recipe program for one dynamically demandable final item."""

    target: Commodity
    unit_raw_cost: Amount
    records: tuple[RomRecipeRecord, ...]

    def __post_init__(self) -> None:
        if self.target.quality is not Quality.LEGENDARY:
            raise QualityPolicyRomError("first ROM ABI supports Legendary mall targets only")
        raw_cost = Fraction(self.unit_raw_cost)
        if raw_cost < 0:
            raise ValueError("unit_raw_cost must be non-negative")
        if not self.records:
            raise ValueError("target policy must contain at least one recipe record")
        ids = [record.recipe_id for record in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("target policy contains duplicate recipe records")
        object.__setattr__(self, "unit_raw_cost", raw_cost)


@dataclass(frozen=True)
class QualityPolicyRom:
    """Integer/static representation consumed by the eventual circuit controller."""

    recipe_names: tuple[str, ...]
    profiles: tuple[RomModuleProfile, ...]
    schedules: tuple[RomSchedule, ...]
    targets: Mapping[Commodity, RomTargetPolicy]

    def __post_init__(self) -> None:
        if len(self.recipe_names) > _MAX_RECIPES:
            raise QualityPolicyRomError(
                f"ROM has {len(self.recipe_names)} recipes; maximum is {_MAX_RECIPES}"
            )
        if len(self.schedules) > _MAX_SCHEDULES:
            raise QualityPolicyRomError(
                f"ROM has {len(self.schedules)} schedules; maximum is {_MAX_SCHEDULES}"
            )
        if not self.targets:
            raise ValueError("ROM must contain at least one target")
        object.__setattr__(self, "targets", MappingProxyType(dict(self.targets)))

    def recipe_name(self, recipe_id: int) -> str:
        return self.recipe_names[recipe_id]

    def target_policy(self, target: Commodity) -> RomTargetPolicy:
        try:
            return self.targets[target]
        except KeyError as exc:
            raise KeyError(
                f"target {target.item}@{target.quality.name.lower()} is not in ROM"
            ) from exc

    def to_json_dict(self) -> dict[str, object]:
        return {
            "abi": {
                "recipe_bits": _RECIPE_BITS,
                "schedule_bits": _SCHEDULE_BITS,
                "record_words": 2,
            },
            "recipes": [
                {"id": index, "name": name}
                for index, name in enumerate(self.recipe_names)
            ],
            "profiles": [
                {
                    "id": index,
                    "name": profile.name,
                    "productivity_modules": profile.productivity_modules,
                    "quality_modules": profile.quality_modules,
                }
                for index, profile in enumerate(self.profiles)
            ],
            "schedules": [
                {
                    "id": index,
                    "choices": [
                        {"profile_id": choice.profile_id, "weight": str(choice.weight)}
                        for choice in schedule.choices
                    ],
                }
                for index, schedule in enumerate(self.schedules)
            ],
            "targets": {
                f"{target.item}@{target.quality.name.lower()}": {
                    "unit_raw_cost": str(policy.unit_raw_cost),
                    "records": [
                        {
                            "recipe_id": record.recipe_id,
                            "recipe": self.recipe_name(record.recipe_id),
                            "schedule_ids": list(record.schedule_ids),
                            "recycle_final": record.recycle_final,
                            "packed": list(record.pack()),
                        }
                        for record in policy.records
                    ],
                }
                for target, policy in sorted(
                    self.targets.items(),
                    key=lambda pair: (pair[0].item, int(pair[0].quality)),
                )
            },
        }


@dataclass(frozen=True)
class RomStorageEstimate:
    """First-order physical storage estimate for signal-keyed target pages."""

    target_count: int
    max_records_per_target: int
    packed_words: int
    constant_combinators_at_20_slots: int


def estimate_signal_keyed_storage(rom: QualityPolicyRom) -> RomStorageEstimate:
    """Estimate a page-oriented constant-combinator ROM.

    Each target contributes two 32-bit words per recipe record.  A Factorio constant
    combinator broadcasts 20 signal values; pages are keyed by target item identity.
    This is deliberately a storage-only lower bound: lookup/gating logic is separate.
    """

    target_count = len(rom.targets)
    max_records = max(len(policy.records) for policy in rom.targets.values())
    packed_words = sum(2 * len(policy.records) for policy in rom.targets.values())
    # In a page layout one combinator can hold 20 target-keyed words for one page.
    pages = 2 * max_records
    constants_per_page = (target_count + 19) // 20
    return RomStorageEstimate(
        target_count=target_count,
        max_records_per_target=max_records,
        packed_words=packed_words,
        constant_combinators_at_20_slots=pages * constants_per_page,
    )


def compile_quality_policy_rom(
    graph: QualityActionGraph,
    policy_book: CompiledQualityPolicyBook,
) -> QualityPolicyRom:
    """Deduplicate module schedules and pack a rich policy book into ROM records."""

    recipe_names = tuple(recipe.name for recipe in graph.recipe_dag.recipes)
    if len(recipe_names) > _MAX_RECIPES:
        raise QualityPolicyRomError(
            f"recipe DAG has {len(recipe_names)} recipes; maximum is {_MAX_RECIPES}"
        )
    recipe_id = {name: index for index, name in enumerate(recipe_names)}
    actions = {action.name: action for action in graph.actions}

    profile_ids: dict[RomModuleProfile, int] = {}
    profiles: list[RomModuleProfile] = []
    schedule_ids: dict[tuple[tuple[int, Fraction], ...], int] = {}
    schedules: list[RomSchedule] = []

    def intern_profile(profile: ModuleProfile) -> int:
        key = RomModuleProfile(profile.productivity_modules, profile.quality_modules)
        existing = profile_ids.get(key)
        if existing is not None:
            return existing
        index = len(profiles)
        profile_ids[key] = index
        profiles.append(key)
        return index

    def intern_schedule(weighted_actions: tuple[WeightedPolicyAction, ...]) -> int:
        raw: list[tuple[int, Fraction]] = []
        for weighted in weighted_actions:
            try:
                action = actions[weighted.action_name]
            except KeyError as exc:
                raise QualityPolicyRomError(
                    f"policy references unknown action {weighted.action_name!r}"
                ) from exc
            raw.append((intern_profile(action.module_profile), Fraction(weighted.weight)))
        combined: dict[int, Fraction] = {}
        for profile, weight in raw:
            combined[profile] = combined.get(profile, Fraction(0)) + weight
        total = sum(combined.values(), start=Fraction(0))
        if total <= 0:
            raise QualityPolicyRomError("compiled schedule has zero total weight")
        key = tuple(
            (profile, weight / total)
            for profile, weight in sorted(combined.items())
            if weight > 0
        )
        existing = schedule_ids.get(key)
        if existing is not None:
            return existing
        index = len(schedules)
        if index >= _MAX_SCHEDULES:
            raise QualityPolicyRomError(
                f"policy requires more than {_MAX_SCHEDULES} distinct schedules"
            )
        schedule_ids[key] = index
        schedules.append(
            RomSchedule(tuple(RomScheduleChoice(profile, weight) for profile, weight in key))
        )
        return index

    targets: dict[Commodity, RomTargetPolicy] = {}
    graph_order = {recipe.name: index for index, recipe in enumerate(graph.recipe_dag.recipes)}
    for target, policy in policy_book.policies.items():
        records: list[RomRecipeRecord] = []
        for name in sorted(policy.recipe_names, key=lambda recipe: graph_order[recipe]):
            if name not in recipe_id:
                raise QualityPolicyRomError(f"policy recipe is absent from DAG: {name!r}")
            quality_schedules: list[int] = []
            for quality in Quality:
                lane = PolicyLane(QualityActionKind.CRAFT, name, quality)
                try:
                    weighted = policy.lane_actions[lane]
                except KeyError as exc:
                    raise QualityPolicyRomError(
                        f"policy lacks craft lane {name}@{quality.name.lower()}"
                    ) from exc
                quality_schedules.append(intern_schedule(weighted))

            recycle_final = any(
                lane.kind is QualityActionKind.RECYCLE and lane.recipe_name == name
                for lane in policy.lane_actions
            )
            records.append(
                RomRecipeRecord(
                    recipe_id=recipe_id[name],
                    schedule_ids=tuple(quality_schedules),  # type: ignore[arg-type]
                    recycle_final=recycle_final,
                )
            )
        targets[target] = RomTargetPolicy(
            target=target,
            unit_raw_cost=policy.unit_raw_cost,
            records=tuple(records),
        )

    return QualityPolicyRom(
        recipe_names=recipe_names,
        profiles=tuple(profiles),
        schedules=tuple(schedules),
        targets=targets,
    )


def _to_u32(value: int) -> int:
    return int(value) & _U32_MASK


def _to_i32(value: int) -> int:
    value = _to_u32(value)
    return value - _I32_MOD if value & _I32_SIGN else value
