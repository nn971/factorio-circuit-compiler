# Autonomous mall research scaffold

This directory is intentionally **not** a finished or accepted autonomous-mall controller.
The earlier manual worker rows, runtime controllers, compiled policy ROMs, and sequential scanners
were exploratory implementations and have been removed. They should not be treated as architectural
precedent.

What remains is the small offline core that is independently useful for future mall work:

- `factorio_data.py` extracts a conservative deterministic item-recipe subset from a real
  Factorio `data-raw-dump.json`;
- `recipe_graph.py` chooses one canonical producer per item and builds an explicit recipe DAG down
  to a configured raw-material boundary;
- `quality_mechanics.py` contains exact expected-value helpers for quality rolls and recycler return;
- `quality_policy_graph.py` expands the DAG into quality-qualified craft/recycle actions for an
  explicitly supplied machine/module profile;
- `linear.py` is the small exact rational LP helper used by the oracle;
- `quality_policy.py` computes the minimum expected prescribed-raw-material use for a stock/target
  snapshot.

## Current economic conventions

These are the conventions represented by the retained quality oracle, not a commitment to a final
circuit architecture.

- Raw materials are a prescribed set of base item names.
- External replenishment is available only for **Normal-quality** instances of prescribed raw items.
- Existing stock at any quality is a free/sunk initial endowment and may be consumed when doing so
  does not violate final requested balances.
- Crafting time is outside the objective; the objective is prescribed raw-material efficiency.
- The first recipe model is conservative: solid item ingredients, one deterministic item product,
  no probabilistic/variable product amount, and one canonical recipe per item with explicit overrides
  available for ambiguity.
- Craft actions consume all ingredients at one exact base quality and may produce that quality or a
  higher one according to the quality distribution.
- The current action graph gives recycle actions only to non-Legendary final target products and uses
  quality modules in the recycler.
- The LP is an **expected-flow oracle**. Fractional action counts are not physical jobs and stochastic
  expected output is never assumed to be real inventory.

The retained oracle is valuable even if the eventual in-game controller uses a completely different
algorithm: it gives a quantitative reference for raw-material efficiency.

## Real Factorio recipe data

Generate a prototype dump with the target Factorio installation, then point the example tools at
`data-raw-dump.json`. `factorio_data.py` deliberately rejects unsupported recipe shapes instead of
silently approximating them.

For example, building the canonical dependency graph for `assembling-machine-2` with a prescribed
plate boundary can be done with:

```bash
uv run python -m examples.autonomous_mall.factorio_data \
  --dump data/data-raw-dump.json \
  --target assembling-machine-2 \
  --raw iron-plate \
  --raw copper-plate \
  --raw steel-plate
```

The graph builder deduplicates shared ancestry, emits recipes in upstream-to-downstream topological
order, and fails on missing producers, unresolved ambiguity, invalid overrides, or dependency cycles.

## Quality oracle

The quality action graph has five exact-quality commodity lanes per required item. For each recipe and
input quality it enumerates the legal productivity/quality module profiles supplied by
`QualityPolicyConfig`; it also contains the first prototype's final-product recycling actions.

The expected-flow LP then solves

```text
minimize weighted Normal-quality prescribed-raw import
subject to expected commodity balance >= target - current stock
```

High-quality raw material already present in stock can therefore enter the corresponding high-quality
lane, but the external raw source remains Normal-only.

A typical oracle invocation is:

```bash
uv run python -m examples.autonomous_mall.quality_policy \
  --dump data/data-raw-dump.json \
  --target assembling-machine-2 \
  --target-quality legendary \
  --amount 1 \
  --raw iron-plate \
  --raw copper-plate \
  --raw steel-plate
```

## What is deliberately unresolved

There is currently **no accepted circuit-side policy representation or autonomous scheduling
algorithm**. Future work should start from the game mechanics and economic invariants rather than from
removed ROM/controller experiments.

In particular, before doing physical-size reasoning, read `docs/factorio-2-circuit-mechanics.md`.
For Factorio 2.x a constant combinator is treated architecturally as a whole-vector source; the legacy
"20 values per constant combinator" model must not be used. The repository intentionally records no
unverified exact numeric capacity.

Any future autonomous design should also preserve the original product requirements: live/variable
demand, live stock as ground truth, prescribed raw-material configuration, multiple workers, clear
productivity/quality worker roles where useful, and anti-oscillation behavior. Those requirements do
not imply any particular ROM or scanner architecture.

## Validation

The retained focused suite is intentionally small:

```bash
uv run pytest \
  tests/examples/autonomous_mall/test_linear.py \
  tests/examples/autonomous_mall/test_quality_mechanics.py \
  tests/examples/autonomous_mall/test_recipe_graph.py \
  tests/examples/autonomous_mall/test_quality_policy_graph.py \
  tests/examples/autonomous_mall/test_quality_policy.py
```

Generic `AssemblerDevice`, anchor-composition, and `ModuleInterface` functionality developed during the
mall experiments lives under `src/factorio_circuit/` and has its own device/synthesis tests. It is
reusable compiler infrastructure and is intentionally retained even though the mall controller
prototype that motivated it has been removed.
