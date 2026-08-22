"""Extract the first autonomous-mall recipe graph from Factorio prototype data.

Factorio 2.x can emit the fully loaded prototype table (including enabled mods) with
``factorio --dump-data``.  The resulting ``data-raw-dump.json`` is the source of
truth for recipe definitions used here.

The first quality-policy prototype accepts only recipes with item ingredients and one
deterministic item product. Unsupported recipes are reported rather than approximated.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .recipe_graph import ItemRecipe, RecipeCatalog, build_recipe_dag


@dataclass(frozen=True)
class ExtractionReport:
    """Counts and reasons for recipe prototypes excluded from the simple catalog."""

    total_prototypes: int
    accepted: int
    ignored_by_reason: Mapping[str, int] = field(default_factory=dict)


class UnsupportedRecipe(ValueError):
    """A recipe lies outside the first prototype's deterministic item-only subset."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _fraction(value: object) -> Fraction:
    if isinstance(value, bool):
        raise TypeError("boolean is not a numeric recipe amount")
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        return Fraction(str(value))
    if isinstance(value, str):
        return Fraction(value)
    raise TypeError(f"unsupported numeric value: {value!r}")


def _ingredient_amount(entry: Mapping[str, Any]) -> Fraction:
    if "amount" not in entry:
        raise UnsupportedRecipe("variable-ingredient-amount")
    amount = _fraction(entry["amount"])
    if amount < 0:
        raise UnsupportedRecipe("negative-ingredient-amount")
    return amount


def _product_amount(entry: Mapping[str, Any]) -> Fraction:
    probability = _fraction(entry.get("probability", 1))
    if probability != 1:
        raise UnsupportedRecipe("probabilistic-product")
    if "amount" in entry:
        amount = _fraction(entry["amount"])
    elif "amount_min" in entry and "amount_max" in entry:
        minimum = _fraction(entry["amount_min"])
        maximum = _fraction(entry["amount_max"])
        if minimum != maximum:
            raise UnsupportedRecipe("variable-product-amount")
        amount = minimum
    else:
        raise UnsupportedRecipe("missing-product-amount")
    if amount <= 0:
        raise UnsupportedRecipe("nonpositive-product-amount")
    return amount


def recipe_from_prototype(name: str, prototype: Mapping[str, Any]) -> ItemRecipe:
    """Convert one Factorio RecipePrototype to the supported mall representation."""

    if prototype.get("hidden", False):
        raise UnsupportedRecipe("hidden")

    ingredients: dict[str, Fraction] = {}
    for entry in prototype.get("ingredients", ()):  # data dump is normalized in 2.x
        if entry.get("type", "item") != "item":
            raise UnsupportedRecipe("fluid-ingredient")
        item = entry.get("name")
        if not isinstance(item, str) or not item:
            raise UnsupportedRecipe("invalid-ingredient")
        amount = _ingredient_amount(entry)
        ingredients[item] = ingredients.get(item, Fraction(0)) + amount

    raw_results = prototype.get("results")
    if raw_results is None and "result" in prototype:
        raw_results = [
            {
                "type": "item",
                "name": prototype["result"],
                "amount": prototype.get("result_count", 1),
            }
        ]
    if raw_results is None:
        raise UnsupportedRecipe("missing-results")

    item_results: dict[str, Fraction] = {}
    for entry in raw_results:
        if entry.get("type", "item") != "item":
            raise UnsupportedRecipe("fluid-product")
        product = entry.get("name")
        if not isinstance(product, str) or not product:
            raise UnsupportedRecipe("invalid-product")
        amount = _product_amount(entry)
        item_results[product] = item_results.get(product, Fraction(0)) + amount

    if len(item_results) != 1:
        raise UnsupportedRecipe("multiple-products")
    product, product_amount = next(iter(item_results.items()))

    return ItemRecipe(
        name=name,
        product=product,
        product_amount=product_amount,
        ingredients=ingredients,
        category=str(prototype.get("category", "crafting")),
        energy_required=_fraction(prototype.get("energy_required", 0.5)),
        allow_productivity=bool(prototype.get("allow_productivity", False)),
        allow_quality=bool(prototype.get("allow_quality", True)),
        maximum_productivity=_fraction(prototype.get("maximum_productivity", 3.0)),
        main_product=(
            str(prototype["main_product"])
            if prototype.get("main_product") not in (None, "")
            else None
        ),
    )


def catalog_from_data_raw(data_raw: Mapping[str, Any]) -> tuple[RecipeCatalog, ExtractionReport]:
    """Build the supported recipe catalog from a decoded ``data.raw`` object."""

    prototypes = data_raw.get("recipe", {})
    if not isinstance(prototypes, Mapping):
        raise ValueError("data.raw.recipe must be an object")

    recipes: list[ItemRecipe] = []
    ignored: dict[str, int] = {}
    for name, prototype in sorted(prototypes.items()):
        if not isinstance(name, str) or not isinstance(prototype, Mapping):
            ignored["invalid-prototype"] = ignored.get("invalid-prototype", 0) + 1
            continue
        try:
            recipes.append(recipe_from_prototype(name, prototype))
        except UnsupportedRecipe as exc:
            ignored[exc.reason] = ignored.get(exc.reason, 0) + 1

    return RecipeCatalog(recipes), ExtractionReport(
        total_prototypes=len(prototypes),
        accepted=len(recipes),
        ignored_by_reason=dict(sorted(ignored.items())),
    )


def load_catalog(path: str | Path) -> tuple[RecipeCatalog, ExtractionReport]:
    """Load ``data-raw-dump.json`` and extract supported mall recipes."""

    dump_path = Path(path)
    with dump_path.open("r", encoding="utf-8") as handle:
        data_raw = json.load(handle)
    if not isinstance(data_raw, Mapping):
        raise ValueError("Factorio data dump root must be an object")
    return catalog_from_data_raw(data_raw)


def run_factorio_dump(
    executable: str | Path,
    *,
    script_output: str | Path,
    config: str | Path | None = None,
    mod_directory: str | Path | None = None,
) -> Path:
    """Run Factorio's official ``--dump-data`` export and return the dump path.

    ``script_output`` is explicit because its location is controlled by Factorio's
    configured write-data/path settings. Passing the same configured mod directory as
    the game makes the dump reflect the player's active prototype set.
    """

    command = [str(executable)]
    if config is not None:
        command += ["--config", str(config)]
    if mod_directory is not None:
        command += ["--mod-directory", str(mod_directory)]
    command += ["--disable-audio", "--dump-data"]
    subprocess.run(command, check=True)

    dump_path = Path(script_output) / "data-raw-dump.json"
    if not dump_path.is_file():
        raise FileNotFoundError(
            f"Factorio completed but {dump_path} was not found; "
            "check the configured script-output directory"
        )
    return dump_path


def _parse_overrides(values: Iterable[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for value in values:
        item, separator, recipe = value.partition("=")
        if not separator or not item or not recipe:
            raise ValueError(f"invalid override {value!r}; expected ITEM=RECIPE")
        overrides[item] = recipe
    return overrides


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a canonical autonomous-mall recipe DAG from Factorio data.raw"
    )
    parser.add_argument("--dump", type=Path, help="existing data-raw-dump.json")
    parser.add_argument("--factorio", type=Path, help="run this Factorio executable first")
    parser.add_argument(
        "--script-output",
        type=Path,
        help="Factorio script-output directory (required with --factorio)",
    )
    parser.add_argument("--config", type=Path, help="optional Factorio config.ini")
    parser.add_argument("--mod-directory", type=Path, help="optional Factorio mod directory")
    parser.add_argument("--target", action="append", required=True, help="target item prototype")
    parser.add_argument("--raw", action="append", default=[], help="raw-boundary item prototype")
    parser.add_argument(
        "--override",
        action="append",
        default=[],
        metavar="ITEM=RECIPE",
        help="manual canonical recipe override",
    )
    parser.add_argument("--output", type=Path, help="write DAG JSON here instead of stdout")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.factorio is not None:
        if args.script_output is None:
            parser.error("--script-output is required with --factorio")
        dump_path = run_factorio_dump(
            args.factorio,
            script_output=args.script_output,
            config=args.config,
            mod_directory=args.mod_directory,
        )
    elif args.dump is not None:
        dump_path = args.dump
    else:
        parser.error("provide either --dump or --factorio")

    try:
        overrides = _parse_overrides(args.override)
    except ValueError as exc:
        parser.error(str(exc))

    catalog, report = load_catalog(dump_path)
    dag = build_recipe_dag(
        catalog,
        targets=args.target,
        raw_items=set(args.raw),
        overrides=overrides,
    )
    document = dag.to_json_dict()
    document["extraction"] = {
        "total_recipe_prototypes": report.total_prototypes,
        "accepted": report.accepted,
        "ignored_by_reason": dict(report.ignored_by_reason),
    }
    rendered = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
