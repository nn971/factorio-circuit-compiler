"""Expand a canonical recipe DAG into quality-qualified craft/recycle actions.

The recipe topology comes from Factorio prototype data.  Worker/module capability is
an explicit policy-compiler input so this layer remains useful for vanilla and modded
machines alike.  The first prototype optimizes raw-material efficiency only: crafting
time and module speed penalties do not enter the action graph.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Sequence

from .factorio_data import load_catalog
from .model import Amount, Commodity, Quality
from .quality_mechanics import expected_quality_outputs, expected_recycler_outputs
from .recipe_graph import ItemRecipe, RecipeDAG, build_recipe_dag


class QualityActionKind(Enum):
    """The two physical transformations in the first quality policy."""

    CRAFT = "craft"
    RECYCLE = "recycle"


@dataclass(frozen=True, order=True)
class ModuleProfile:
    """One fixed worker module profile and its aggregate useful effects.

    The counts identify the physical worker pool later.  Effects are aggregate bonuses
    after module quality has already been accounted for by the offline configuration.
    Speed/energy/pollution effects are deliberately absent because the prototype's
    economic objective is raw-material efficiency.
    """

    productivity_modules: int
    quality_modules: int
    productivity_bonus: Amount
    quality_chance: Amount

    def __post_init__(self) -> None:
        if self.productivity_modules < 0 or self.quality_modules < 0:
            raise ValueError("module counts must be non-negative")
        productivity = Fraction(self.productivity_bonus)
        quality = Fraction(self.quality_chance)
        if productivity < 0:
            raise ValueError("productivity_bonus must be non-negative")
        if quality < 0 or quality > 1:
            raise ValueError("quality_chance must lie in [0, 1]")
        object.__setattr__(self, "productivity_bonus", productivity)
        object.__setattr__(self, "quality_chance", quality)

    @property
    def name(self) -> str:
        return f"{self.productivity_modules}p{self.quality_modules}q"


@dataclass(frozen=True)
class QualityPolicyConfig:
    """Fixed module capabilities used while compiling the offline policy graph.

    Defaults describe a useful late-game vanilla benchmark: four crafting slots with
    legendary Productivity 3 (+25%) or Quality 3 (+6.25%) modules, and a four-slot
    quality recycler.  Callers may override every value for other machines/mods.
    """

    module_slots: int = 4
    productivity_bonus_per_module: Amount = Fraction(1, 4)
    quality_chance_per_module: Amount = Fraction(1, 16)
    recycler_module_slots: int = 4
    recycler_quality_chance_per_module: Amount = Fraction(1, 16)
    prune_dominated_profiles: bool = True

    def __post_init__(self) -> None:
        if self.module_slots < 0 or self.recycler_module_slots < 0:
            raise ValueError("module slot counts must be non-negative")
        productivity = Fraction(self.productivity_bonus_per_module)
        quality = Fraction(self.quality_chance_per_module)
        recycler_quality = Fraction(self.recycler_quality_chance_per_module)
        if productivity < 0:
            raise ValueError("productivity_bonus_per_module must be non-negative")
        if quality < 0 or recycler_quality < 0:
            raise ValueError("quality chance per module must be non-negative")
        if quality * self.module_slots > 1:
            raise ValueError("aggregate crafting quality chance exceeds 100%")
        if recycler_quality * self.recycler_module_slots > 1:
            raise ValueError("aggregate recycler quality chance exceeds 100%")
        object.__setattr__(self, "productivity_bonus_per_module", productivity)
        object.__setattr__(self, "quality_chance_per_module", quality)
        object.__setattr__(self, "recycler_quality_chance_per_module", recycler_quality)


@dataclass(frozen=True)
class QualityAction:
    """One expected material transformation available to the offline policy solver."""

    name: str
    kind: QualityActionKind
    recipe_name: str
    base_quality: Quality
    module_profile: ModuleProfile
    inputs: Mapping[Commodity, Amount]
    outputs: Mapping[Commodity, Amount]

    def __post_init__(self) -> None:
        inputs = {commodity: Fraction(amount) for commodity, amount in self.inputs.items() if amount}
        outputs = {commodity: Fraction(amount) for commodity, amount in self.outputs.items() if amount}
        if any(amount < 0 for amount in inputs.values()):
            raise ValueError("action inputs must be non-negative")
        if any(amount < 0 for amount in outputs.values()):
            raise ValueError("action outputs must be non-negative")
        if not outputs:
            raise ValueError("action must have a positive expected output")
        object.__setattr__(self, "inputs", MappingProxyType(inputs))
        object.__setattr__(self, "outputs", MappingProxyType(outputs))


@dataclass(frozen=True)
class QualityActionGraph:
    """Five-quality state expansion of one canonical recipe DAG."""

    recipe_dag: RecipeDAG
    config: QualityPolicyConfig
    commodities: tuple[Commodity, ...]
    actions: tuple[QualityAction, ...]

    def actions_for(self, recipe_name: str, base_quality: Quality) -> tuple[QualityAction, ...]:
        return tuple(
            action
            for action in self.actions
            if action.recipe_name == recipe_name and action.base_quality is base_quality
        )

    def to_json_dict(self) -> dict[str, object]:
        def commodity_dict(values: Mapping[Commodity, Amount]) -> dict[str, str]:
            return {
                f"{commodity.item}@{commodity.quality.name.lower()}": str(amount)
                for commodity, amount in sorted(
                    values.items(), key=lambda pair: (pair[0].item, int(pair[0].quality))
                )
            }

        return {
            "targets": list(self.recipe_dag.targets),
            "raw_items": sorted(self.recipe_dag.raw_items),
            "config": {
                "module_slots": self.config.module_slots,
                "productivity_bonus_per_module": str(self.config.productivity_bonus_per_module),
                "quality_chance_per_module": str(self.config.quality_chance_per_module),
                "recycler_module_slots": self.config.recycler_module_slots,
                "recycler_quality_chance_per_module": str(
                    self.config.recycler_quality_chance_per_module
                ),
                "prune_dominated_profiles": self.config.prune_dominated_profiles,
            },
            "commodity_count": len(self.commodities),
            "action_count": len(self.actions),
            "actions": [
                {
                    "name": action.name,
                    "kind": action.kind.value,
                    "recipe": action.recipe_name,
                    "base_quality": action.base_quality.name.lower(),
                    "module_profile": {
                        "name": action.module_profile.name,
                        "productivity_modules": action.module_profile.productivity_modules,
                        "quality_modules": action.module_profile.quality_modules,
                        "productivity_bonus": str(action.module_profile.productivity_bonus),
                        "quality_chance": str(action.module_profile.quality_chance),
                    },
                    "inputs": commodity_dict(action.inputs),
                    "outputs": commodity_dict(action.outputs),
                }
                for action in self.actions
            ],
        }


def _module_profile(
    recipe: ItemRecipe,
    *,
    productivity_modules: int,
    quality_modules: int,
    config: QualityPolicyConfig,
) -> ModuleProfile:
    productivity = min(
        Fraction(productivity_modules) * config.productivity_bonus_per_module,
        recipe.maximum_productivity,
    )
    quality = Fraction(quality_modules) * config.quality_chance_per_module
    return ModuleProfile(
        productivity_modules=productivity_modules,
        quality_modules=quality_modules,
        productivity_bonus=productivity,
        quality_chance=quality,
    )


def _candidate_profiles(
    recipe: ItemRecipe,
    *,
    base_quality: Quality,
    config: QualityPolicyConfig,
) -> tuple[ModuleProfile, ...]:
    slots = config.module_slots

    # At legendary, quality has no material effect.  If productivity is legal it
    # strictly dominates spending any slot on quality for our raw-efficiency objective.
    if base_quality is Quality.LEGENDARY and recipe.allow_productivity:
        return (
            _module_profile(
                recipe,
                productivity_modules=slots,
                quality_modules=0,
                config=config,
            ),
        )

    if recipe.allow_productivity and recipe.allow_quality:
        pairs = ((p, slots - p) for p in range(slots + 1))
    elif recipe.allow_productivity:
        pairs = ((slots, 0),)
    elif recipe.allow_quality:
        pairs = ((0, slots),)
    else:
        pairs = ((0, 0),)

    return tuple(
        _module_profile(
            recipe,
            productivity_modules=productivity_modules,
            quality_modules=quality_modules,
            config=config,
        )
        for productivity_modules, quality_modules in pairs
    )


def _craft_action(
    recipe: ItemRecipe,
    *,
    base_quality: Quality,
    profile: ModuleProfile,
) -> QualityAction:
    inputs = {
        Commodity(item, base_quality): Fraction(amount)
        for item, amount in recipe.ingredients.items()
    }
    outputs = expected_quality_outputs(
        item=recipe.product,
        base_quality=base_quality,
        output_amount=recipe.product_amount * (1 + profile.productivity_bonus),
        quality_chance=profile.quality_chance,
    )
    return QualityAction(
        name=f"craft:{recipe.name}:{base_quality.name.lower()}:{profile.name}",
        kind=QualityActionKind.CRAFT,
        recipe_name=recipe.name,
        base_quality=base_quality,
        module_profile=profile,
        inputs=inputs,
        outputs=outputs,
    )


def _dominates(left: QualityAction, right: QualityAction) -> bool:
    if left.inputs != right.inputs:
        return False
    commodities = set(left.outputs) | set(right.outputs)
    weakly_better = all(left.outputs.get(c, Fraction(0)) >= right.outputs.get(c, Fraction(0)) for c in commodities)
    strictly_better = any(left.outputs.get(c, Fraction(0)) > right.outputs.get(c, Fraction(0)) for c in commodities)
    return weakly_better and strictly_better


def _prune_dominated(actions: Sequence[QualityAction]) -> tuple[QualityAction, ...]:
    kept: list[QualityAction] = []
    for candidate in actions:
        if any(_dominates(other, candidate) for other in actions if other is not candidate):
            continue
        kept.append(candidate)
    return tuple(kept)


def _craft_actions_for_recipe(
    recipe: ItemRecipe,
    *,
    config: QualityPolicyConfig,
) -> tuple[QualityAction, ...]:
    result: list[QualityAction] = []
    for base_quality in Quality:
        candidates = tuple(
            _craft_action(recipe, base_quality=base_quality, profile=profile)
            for profile in _candidate_profiles(recipe, base_quality=base_quality, config=config)
        )
        if config.prune_dominated_profiles:
            candidates = _prune_dominated(candidates)
        result.extend(candidates)
    return tuple(result)


def _recycler_actions(
    recipe: ItemRecipe,
    *,
    config: QualityPolicyConfig,
) -> tuple[QualityAction, ...]:
    quality_modules = config.recycler_module_slots
    quality_chance = quality_modules * config.recycler_quality_chance_per_module
    profile = ModuleProfile(
        productivity_modules=0,
        quality_modules=quality_modules,
        productivity_bonus=0,
        quality_chance=quality_chance,
    )
    actions: list[QualityAction] = []
    for recycled_quality in Quality:
        if recycled_quality is Quality.LEGENDARY:
            continue
        actions.append(
            QualityAction(
                name=f"recycle:{recipe.name}:{recycled_quality.name.lower()}:{profile.name}",
                kind=QualityActionKind.RECYCLE,
                recipe_name=recipe.name,
                base_quality=recycled_quality,
                module_profile=profile,
                inputs={Commodity(recipe.product, recycled_quality): Fraction(1)},
                outputs=expected_recycler_outputs(
                    ingredients_per_recipe=recipe.ingredients,
                    recipe_output_amount=recipe.product_amount,
                    recycled_quality=recycled_quality,
                    quality_chance=quality_chance,
                ),
            )
        )
    return tuple(actions)


def build_quality_action_graph(
    recipe_dag: RecipeDAG,
    *,
    config: QualityPolicyConfig | None = None,
) -> QualityActionGraph:
    """Expand ``recipe_dag`` into five quality lanes and legal module actions.

    Prototype policy:
      * every canonical recipe may craft at every quality tier;
      * all machine slots are filled with productivity and/or quality modules whenever
        those effects are legal, because crafting time is outside the objective;
      * dominated module profiles are removed;
      * only non-legendary *final target products* receive recycle actions;
      * recycler slots contain quality modules only.
    """

    resolved = config or QualityPolicyConfig()
    commodities = tuple(
        Commodity(item, quality)
        for item in sorted(recipe_dag.required_items)
        for quality in Quality
    )
    actions: list[QualityAction] = []
    for recipe in recipe_dag.recipes:
        actions.extend(_craft_actions_for_recipe(recipe, config=resolved))

    for target in recipe_dag.targets:
        recipe = recipe_dag.recipe_for(target)
        if recipe is None:
            raise ValueError(f"target {target!r} lies on the raw boundary and cannot be recycled")
        actions.extend(_recycler_actions(recipe, config=resolved))

    return QualityActionGraph(
        recipe_dag=recipe_dag,
        config=resolved,
        commodities=commodities,
        actions=tuple(actions),
    )


def _fraction_argument(value: str) -> Fraction:
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise argparse.ArgumentTypeError(f"invalid rational value: {value!r}") from exc


def _parse_overrides(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        item, separator, recipe = value.partition("=")
        if not separator or not item or not recipe:
            raise ValueError(f"invalid override {value!r}; expected ITEM=RECIPE")
        result[item] = recipe
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Expand a Factorio recipe DAG into quality actions")
    parser.add_argument("--dump", type=Path, required=True, help="data-raw-dump.json")
    parser.add_argument("--target", action="append", required=True, help="target item prototype")
    parser.add_argument("--raw", action="append", default=[], help="raw-boundary item prototype")
    parser.add_argument("--override", action="append", default=[], metavar="ITEM=RECIPE")
    parser.add_argument("--module-slots", type=int, default=4)
    parser.add_argument("--productivity-per-module", type=_fraction_argument, default=Fraction(1, 4))
    parser.add_argument("--quality-per-module", type=_fraction_argument, default=Fraction(1, 16))
    parser.add_argument("--recycler-slots", type=int, default=4)
    parser.add_argument(
        "--recycler-quality-per-module", type=_fraction_argument, default=Fraction(1, 16)
    )
    parser.add_argument("--keep-dominated", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        overrides = _parse_overrides(args.override)
    except ValueError as exc:
        parser.error(str(exc))

    catalog, extraction = load_catalog(args.dump)
    dag = build_recipe_dag(
        catalog,
        targets=args.target,
        raw_items=set(args.raw),
        overrides=overrides,
    )
    config = QualityPolicyConfig(
        module_slots=args.module_slots,
        productivity_bonus_per_module=args.productivity_per_module,
        quality_chance_per_module=args.quality_per_module,
        recycler_module_slots=args.recycler_slots,
        recycler_quality_chance_per_module=args.recycler_quality_per_module,
        prune_dominated_profiles=not args.keep_dominated,
    )
    graph = build_quality_action_graph(dag, config=config)
    document = graph.to_json_dict()
    document["extraction"] = {
        "total_recipe_prototypes": extraction.total_prototypes,
        "accepted": extraction.accepted,
        "ignored_by_reason": dict(extraction.ignored_by_reason),
    }
    rendered = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
