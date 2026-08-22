# Autonomous mall research scaffold

This directory is intentionally a research area rather than an accepted final circuit architecture.
The earlier manual worker rows, runtime controllers, compiled policy ROMs, and sequential scanners
were exploratory implementations and have been removed. They should not be treated as architectural
precedent.

Two independent reference layers now live here:

- a small deterministic, quality-free multi-worker scheduler prototype in `scheduler.py`;
- the retained offline quality/material-efficiency oracle used for later quality-policy experiments.

Generic `AssemblerDevice`, anchor-composition, and `ModuleInterface` functionality developed during
mall experiments lives under `src/factorio_circuit/` and remains reusable compiler infrastructure.

## Deterministic multi-worker scheduler prototype

`scheduler.py` is the first reference policy for the simplified milestone where recipes are
deterministic and quality is irrelevant. It deliberately contains no raw-material objective and no
configured raw-material boundary.

Its inputs are:

- a target stock vector;
- one observed physical stock vector;
- the jobs already active on the anonymous worker pool;
- a deterministic `RecipeCatalog` with one canonical producer selected for each producible item.

Items with no supported producer are inferred to be external inputs. Multiple producers retain the
existing deterministic `RecipeCatalog` rule: an explicit override wins, otherwise a unique producer
or the conventional same-name producer is selected, and unresolved ambiguity is reported.

For every planning pass the scheduler:

1. subtracts the input reservations of active jobs from usable physical stock;
2. adds their deterministic outputs as promised future stock;
3. recursively expands target shortages through the selected recipes;
4. records any shortage that bottoms out at an unproducible item as `blocked_external`;
5. orders required recipes from upstream dependencies toward consumers;
6. dispatches only recipes whose direct ingredients can be reserved immediately;
7. splits work into bounded batches and fills at most the currently free worker slots;
8. reserves every newly dispatched batch before considering the next worker.

The two anti-oscillation ledgers are therefore explicit:

```text
reserved input   = material already committed to active/new jobs
promised output  = deterministic output already committed by active/new jobs
```

A target already covered by promised output does not launch duplicate work, and two workers cannot
reserve the same physical ingredient stock.

`max_batch_crafts` is only an execution bound. It does not represent an optimization objective. A
later controller may replace it with a better batching policy without changing the reservation and
promise semantics.

### Stock snapshot contract

For this first offline prototype, `stock` means mall-owned physical inventory **before** subtracting
active-job reservations. Active-job inputs remain part of that snapshot until the reference
`complete_jobs()` transition consumes them. This makes the accounting model unambiguous and lets the
scheduler reject inconsistent snapshots where active reservations exceed physical stock.

A future Factorio integration must map roboport stock plus any worker-local holdings to this logical
quantity according to the actual device protocol. That mapping is deliberately outside this offline
scheduler.

### Small example

```python
from examples.autonomous_mall import (
    DeterministicMallScheduler,
    ItemRecipe,
    RecipeCatalog,
)

catalog = RecipeCatalog(
    [
        ItemRecipe("cable", "cable", 2, {"copper": 1}),
        ItemRecipe("circuit", "circuit", 1, {"iron": 1, "cable": 3}),
    ]
)

scheduler = DeterministicMallScheduler(
    catalog,
    worker_count=2,
    max_batch_crafts=2,
)
plan = scheduler.plan(
    targets={"circuit": 2},
    stock={"iron": 2, "copper": 3},
)
```

With no cable initially present, both free workers are assigned cable batches first. The circuit jobs
remain planned but undispatched until their direct cable inputs physically exist.

## Retained quality oracle

The older offline quality oracle remains useful as a separate future reference. Its conventions are
specific to that oracle and are not requirements of the simplified deterministic scheduler.

The retained pieces are:

- `factorio_data.py`: extracts a conservative deterministic item-recipe subset from a real Factorio
  `data-raw-dump.json`;
- `recipe_graph.py`: canonical recipe selection and explicit recipe DAG construction;
- `quality_mechanics.py`: exact expected-value helpers for quality rolls and recycler return;
- `quality_policy_graph.py`: quality-qualified craft/recycle actions for an explicitly supplied
  machine/module profile;
- `linear.py`: the exact rational LP helper;
- `quality_policy.py`: minimum expected prescribed-raw-material use for a stock/target snapshot.

### Quality-oracle economic conventions

These conventions describe only the retained quality oracle:

- raw materials are a prescribed set of base item names;
- external replenishment is available only for Normal-quality instances of prescribed raw items;
- existing stock at any quality is a free/sunk initial endowment and may be consumed when doing so
  does not violate final requested balances;
- crafting time is outside the objective;
- the first recipe model uses solid item ingredients, one deterministic item product, and one
  canonical recipe per item with explicit overrides for ambiguity;
- craft actions consume ingredients at one exact base quality and may roll higher-quality outputs;
- the current action graph gives recycle actions only to non-Legendary final target products;
- the LP is an expected-flow oracle, so fractional action counts are not physical jobs.

## Real Factorio recipe data

Generate a prototype dump with the target Factorio installation, then point the example tools at
`data-raw-dump.json`. `factorio_data.py` deliberately rejects unsupported recipe shapes instead of
silently approximating them.

For example, the older explicit-boundary DAG tool can be run with:

```bash
uv run python -m examples.autonomous_mall.factorio_data \
  --dump data/data-raw-dump.json \
  --target assembling-machine-2 \
  --raw iron-plate \
  --raw copper-plate \
  --raw steel-plate
```

That explicit raw boundary belongs to the DAG/quality-oracle workflow. The deterministic scheduler
itself infers an external input simply when its `RecipeCatalog` has no producer for that item.

## Quality oracle invocation

A typical quality-oracle invocation is:

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

There is still no accepted **circuit-side** autonomous scheduling architecture. In particular, the
Python scheduler does not decide:

- how target, stock, reservation, promise, recipe, and worker-state vectors are encoded physically;
- how a worker claims a dispatched batch;
- how requester demand is materialized and cleared;
- how the logical stock snapshot is reconstructed from roboport and worker-local observations;
- how batch size should adapt in the final controller;
- how the scheduler discovers/query recipes in-game without a combinator count growing with the
  recipe database;
- how the eventual quality/productivity worker architecture should extend this deterministic core.

Before doing physical-size reasoning, read `docs/factorio-2-circuit-mechanics.md`. For Factorio 2.x a
constant combinator is treated architecturally as a whole-vector source; the legacy "20 values per
constant combinator" model must not be used. The repository intentionally records no unverified exact
numeric capacity.

## Validation

Focused deterministic scheduler tests:

```bash
uv run pytest tests/examples/autonomous_mall/test_scheduler.py
```

The retained quality-oracle suite is:

```bash
uv run pytest \
  tests/examples/autonomous_mall/test_linear.py \
  tests/examples/autonomous_mall/test_quality_mechanics.py \
  tests/examples/autonomous_mall/test_recipe_graph.py \
  tests/examples/autonomous_mall/test_quality_policy_graph.py \
  tests/examples/autonomous_mall/test_quality_policy.py
```
