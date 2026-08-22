"""Offline expected-flow optimizer for one compiled quality action graph.

The solver is deliberately example-local.  It treats actual stock as free initial
endowment and permits external supply only for Normal-quality prescribed raw items.
Its output is a fractional expected-flow policy; the runtime controller will later
execute only a small receding-horizon tranche and replan from actual RNG outcomes.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Mapping, Sequence

from .factorio_data import load_catalog
from .linear import InfeasibleLinearProgram, minimize_covering
from .model import Amount, Commodity, Quality
from .quality_policy_graph import (
    QualityAction,
    QualityActionGraph,
    QualityPolicyConfig,
    build_quality_action_graph,
)
from .recipe_graph import build_recipe_dag


class QualityPolicyError(RuntimeError):
    """The expected quality policy cannot satisfy the requested target."""


@dataclass(frozen=True)
class QualityPlanStep:
    """Expected number of executions of one quality action."""

    action: QualityAction
    expected_runs: Amount


@dataclass(frozen=True)
class QualityPlan:
    """Minimum-raw expected material flow for one stock/target snapshot."""

    raw_required: Mapping[Commodity, Amount]
    steps: tuple[QualityPlanStep, ...]
    expected_surplus: Mapping[Commodity, Amount]

    @property
    def raw_total(self) -> Amount:
        return sum(self.raw_required.values(), start=Fraction(0))

    def to_json_dict(self) -> dict[str, object]:
        def key(commodity: Commodity) -> str:
            return f"{commodity.item}@{commodity.quality.name.lower()}"

        return {
            "raw_total": str(self.raw_total),
            "raw_required": {
                key(commodity): str(amount)
                for commodity, amount in sorted(
                    self.raw_required.items(), key=lambda pair: (pair[0].item, int(pair[0].quality))
                )
            },
            "steps": [
                {
                    "action": step.action.name,
                    "kind": step.action.kind.value,
                    "recipe": step.action.recipe_name,
                    "base_quality": step.action.base_quality.name.lower(),
                    "module_profile": step.action.module_profile.name,
                    "expected_runs": str(step.expected_runs),
                }
                for step in self.steps
            ],
            "expected_surplus": {
                key(commodity): str(amount)
                for commodity, amount in sorted(
                    self.expected_surplus.items(),
                    key=lambda pair: (pair[0].item, int(pair[0].quality)),
                )
            },
        }


def solve_quality_policy(
    graph: QualityActionGraph,
    *,
    targets: Mapping[Commodity, Amount],
    stock: Mapping[Commodity, Amount] | None = None,
    raw_costs: Mapping[str, Amount] | None = None,
) -> QualityPlan:
    """Minimize additional Normal-quality raw import for the compiled action graph.

    ``targets`` and ``stock`` may reference any commodity already represented by the
    graph.  Existing high-quality stock is therefore automatically treated as a free
    shortcut into the corresponding quality lane.  External raw supply is Normal-only
    in this first prototype.
    """

    stock = stock or {}
    targets = {commodity: Fraction(amount) for commodity, amount in targets.items() if amount}
    stock = {commodity: Fraction(amount) for commodity, amount in stock.items() if amount}
    if any(amount < 0 for amount in targets.values()):
        raise ValueError("targets must be non-negative")
    if any(amount < 0 for amount in stock.values()):
        raise ValueError("stock must be non-negative")

    commodities = tuple(graph.commodities)
    known = set(commodities)
    unknown = (set(targets) | set(stock)) - known
    if unknown:
        rendered = ", ".join(
            f"{c.item}@{c.quality.name.lower()}" for c in sorted(unknown, key=lambda c: (c.item, int(c.quality)))
        )
        raise ValueError(f"commodities outside quality action graph: {rendered}")

    row_of = {commodity: index for index, commodity in enumerate(commodities)}
    columns: list[dict[int, Amount]] = []
    costs: list[Amount] = []

    for action in graph.actions:
        column: dict[int, Amount] = {}
        for commodity, amount in action.outputs.items():
            row = row_of[commodity]
            column[row] = column.get(row, Fraction(0)) + Fraction(amount)
        for commodity, amount in action.inputs.items():
            row = row_of[commodity]
            column[row] = column.get(row, Fraction(0)) - Fraction(amount)
            if not column[row]:
                del column[row]
        columns.append(column)
        costs.append(Fraction(0))

    raw_variables: list[Commodity] = []
    for item in sorted(graph.recipe_dag.raw_items):
        commodity = Commodity(item, Quality.NORMAL)
        columns.append({row_of[commodity]: Fraction(1)})
        weight = Fraction(raw_costs[item]) if raw_costs and item in raw_costs else Fraction(1)
        if weight < 0:
            raise ValueError(f"raw cost for {item!r} must be non-negative")
        costs.append(weight)
        raw_variables.append(commodity)

    lower_bounds = [
        targets.get(commodity, Fraction(0)) - stock.get(commodity, Fraction(0))
        for commodity in commodities
    ]
    try:
        solution = minimize_covering(costs=costs, columns=columns, lower_bounds=lower_bounds)
    except InfeasibleLinearProgram as exc:
        raise QualityPolicyError("quality target is infeasible from configured raw boundary") from exc

    action_values = solution.variables[: len(graph.actions)]
    raw_values = solution.variables[len(graph.actions) :]
    steps = tuple(
        QualityPlanStep(action=action, expected_runs=runs)
        for action, runs in zip(graph.actions, action_values, strict=True)
        if runs
    )
    raw_required = {
        commodity: amount
        for commodity, amount in zip(raw_variables, raw_values, strict=True)
        if amount
    }

    final_balance = {commodity: stock.get(commodity, Fraction(0)) for commodity in commodities}
    for action, runs in zip(graph.actions, action_values, strict=True):
        if not runs:
            continue
        for commodity, amount in action.outputs.items():
            final_balance[commodity] = final_balance.get(commodity, Fraction(0)) + runs * amount
        for commodity, amount in action.inputs.items():
            final_balance[commodity] = final_balance.get(commodity, Fraction(0)) - runs * amount
    for commodity, amount in raw_required.items():
        final_balance[commodity] = final_balance.get(commodity, Fraction(0)) + amount

    surplus = {
        commodity: balance - targets.get(commodity, Fraction(0))
        for commodity, balance in final_balance.items()
        if balance > targets.get(commodity, Fraction(0))
    }
    return QualityPlan(raw_required=raw_required, steps=steps, expected_surplus=surplus)


def _fraction_argument(value: str) -> Fraction:
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise argparse.ArgumentTypeError(f"invalid rational value: {value!r}") from exc


def _quality_argument(value: str) -> Quality:
    normalized = value.strip().upper()
    try:
        return Quality[normalized]
    except KeyError as exc:
        choices = ", ".join(q.name.lower() for q in Quality)
        raise argparse.ArgumentTypeError(f"unknown quality {value!r}; choose one of {choices}") from exc


def _parse_overrides(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        item, separator, recipe = value.partition("=")
        if not separator or not item or not recipe:
            raise ValueError(f"invalid override {value!r}; expected ITEM=RECIPE")
        result[item] = recipe
    return result


def _parse_stock(values: Sequence[str]) -> dict[Commodity, Amount]:
    result: dict[Commodity, Amount] = {}
    for value in values:
        lhs, separator, amount_text = value.partition("=")
        if not separator:
            raise ValueError(f"invalid stock {value!r}; expected ITEM@QUALITY=AMOUNT")
        item, at, quality_text = lhs.partition("@")
        if not at or not item or not quality_text:
            raise ValueError(f"invalid stock {value!r}; expected ITEM@QUALITY=AMOUNT")
        try:
            quality = Quality[quality_text.upper()]
            amount = Fraction(amount_text)
        except (KeyError, ValueError, ZeroDivisionError) as exc:
            raise ValueError(f"invalid stock {value!r}; expected ITEM@QUALITY=AMOUNT") from exc
        if amount < 0:
            raise ValueError("stock amount must be non-negative")
        commodity = Commodity(item, quality)
        result[commodity] = result.get(commodity, Fraction(0)) + amount
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Solve the first offline Factorio quality policy")
    parser.add_argument("--dump", type=Path, required=True, help="data-raw-dump.json")
    parser.add_argument("--target", required=True, help="target item prototype")
    parser.add_argument("--target-quality", type=_quality_argument, default=Quality.LEGENDARY)
    parser.add_argument("--amount", type=_fraction_argument, default=Fraction(1))
    parser.add_argument("--raw", action="append", default=[], help="raw-boundary item prototype")
    parser.add_argument("--override", action="append", default=[], metavar="ITEM=RECIPE")
    parser.add_argument(
        "--stock",
        action="append",
        default=[],
        metavar="ITEM@QUALITY=AMOUNT",
        help="free initial stock; may be repeated",
    )
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
    if args.amount <= 0:
        parser.error("--amount must be positive")
    try:
        overrides = _parse_overrides(args.override)
        stock = _parse_stock(args.stock)
    except ValueError as exc:
        parser.error(str(exc))

    catalog, extraction = load_catalog(args.dump)
    dag = build_recipe_dag(
        catalog,
        targets=[args.target],
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
    target = Commodity(args.target, args.target_quality)
    plan = solve_quality_policy(graph, targets={target: args.amount}, stock=stock)
    document = plan.to_json_dict()
    document["target"] = {
        "item": args.target,
        "quality": args.target_quality.name.lower(),
        "amount": str(args.amount),
    }
    document["candidate_action_count"] = len(graph.actions)
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
