"""Compile a real Factorio data dump into the first compact autonomous-mall ROM."""

from __future__ import annotations

import argparse
import json
from fractions import Fraction
from pathlib import Path
from typing import Sequence

from .compiled_quality_policy import compile_quality_policy_book
from .factorio_data import load_catalog
from .quality_policy_graph import QualityPolicyConfig, build_quality_action_graph
from .quality_policy_rom import compile_quality_policy_rom, estimate_signal_keyed_storage
from .recipe_graph import build_recipe_dag
from .signal_keyed_policy_rom import (
    build_recipe_address_vector,
    build_signal_keyed_policy_pages,
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
    parser = argparse.ArgumentParser(
        description="Compile Factorio recipes into compact variable-demand quality-policy ROM"
    )
    parser.add_argument("--dump", type=Path, required=True, help="data-raw-dump.json")
    parser.add_argument(
        "--target",
        action="append",
        required=True,
        help="Legendary mall target item; may be repeated",
    )
    parser.add_argument("--raw", action="append", default=[], help="Normal-only external raw item")
    parser.add_argument("--override", action="append", default=[], metavar="ITEM=RECIPE")
    parser.add_argument("--module-slots", type=int, default=4)
    parser.add_argument("--productivity-per-module", type=_fraction_argument, default=Fraction(1, 4))
    parser.add_argument("--quality-per-module", type=_fraction_argument, default=Fraction(1, 16))
    parser.add_argument("--recycler-slots", type=int, default=4)
    parser.add_argument(
        "--recycler-quality-per-module",
        type=_fraction_argument,
        default=Fraction(1, 16),
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
    book = compile_quality_policy_book(graph)
    rom = compile_quality_policy_rom(graph, book)
    pages = build_signal_keyed_policy_pages(rom)
    addresses = build_recipe_address_vector(graph, rom)
    estimate = estimate_signal_keyed_storage(rom)
    address_constants = len(addresses.constant_chunks())

    document = rom.to_json_dict()
    document["summary"] = {
        "targets": len(rom.targets),
        "canonical_recipes": len(rom.recipe_names),
        "physical_module_profiles": len(rom.profiles),
        "distinct_schedules": len(rom.schedules),
        "packed_recipe_records": sum(len(policy.records) for policy in rom.targets.values()),
        "packed_words": estimate.packed_words,
        "max_records_per_target": estimate.max_records_per_target,
        "signal_keyed_pages": len(pages.pages),
        "policy_page_constant_combinators_exact_at_20_slots": pages.constant_combinator_count,
        "recipe_address_constant_combinators_at_20_slots": address_constants,
        "static_rom_constant_combinators_exact_at_20_slots": (
            pages.constant_combinator_count + address_constants
        ),
        "policy_page_constant_combinators_rectangular_estimate_at_20_slots": (
            estimate.constant_combinators_at_20_slots
        ),
    }
    document["recipe_address_vector"] = dict(sorted(addresses.entries.items()))
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
