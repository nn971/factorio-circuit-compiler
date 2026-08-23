# Autonomous mall research scaffold

This directory is a research area rather than an accepted final circuit architecture. Earlier
manual worker rows, compiled policy ROMs, and sequential scanners were exploratory implementations
and should not be treated as architectural precedent.

The retained pieces now form three deliberately separate layers:

- `scheduler.py`: an offline deterministic, quality-free multi-worker scheduling oracle;
- `worker_pool.py`: the first circuit-facing anonymous-worker dispatch/execution protocol;
- the older quality/recycling modules: an offline material-efficiency oracle for later milestones.

Generic `AssemblerDevice`, exact-overlap anchors, and `ModuleInterface` live under
`src/factorio_circuit/` and remain reusable compiler infrastructure.

## Deterministic scheduler

`scheduler.py` models the simplified milestone where recipes are deterministic and quality is
irrelevant. It contains no raw-material objective and no configured raw-material boundary.

Its inputs are a target-stock vector, observed physical stock, already-active jobs, and a deterministic
`RecipeCatalog`. Items with no supported producer are inferred to be external inputs. Multiple
producers use the catalog's canonical selection/override rule.

Each planning pass:

1. subtracts active-job input reservations from usable stock;
2. adds active-job deterministic outputs as promised future stock;
3. recursively expands target shortages through selected recipes;
4. reports shortages that bottom out at an unproducible item as `blocked_external`;
5. orders required recipes from dependencies toward consumers;
6. dispatches only recipes whose direct ingredients can be reserved immediately;
7. splits work into bounded batches and fills at most the free worker slots;
8. reserves each newly dispatched batch before considering the next worker.

The anti-oscillation ledgers are explicit:

```text
reserved input   = material already committed to active/new jobs
promised output  = deterministic output already committed by active/new jobs
```

For this offline reference, `stock` means mall-owned physical inventory before subtracting active-job
reservations. A future Factorio integration must reconstruct that logical quantity from the roboport
view plus worker-local holdings according to the chosen device protocol.

## Circuit-facing worker pool

`worker_pool.py` implements the next layer down. It accepts one already-formed deterministic craft
job and routes it to the first idle anonymous worker.

### One-craft job envelope

The first physical protocol intentionally makes one accepted envelope equal to **one craft**:

```text
offer_valid    scalar Level
offer_recipe   vector Level
offer_inputs   vector Level   exact ingredients for one craft
offer_product  vector Level   exact deterministic products for one craft
```

`AssemblerDevice.requester_demand` is a steady-state logistic setpoint. Using a whole multi-craft
batch as that setpoint while the machine consumes ingredients would cause continued replenishment.
Keeping the first protocol one-craft makes the requester semantics exact. Batching can later be added
with an explicit preload/escrow protocol while preserving the same reservation/promise accounting.

### Offer handshake

The producer may hold one envelope high until it observes `offer_accepted`. It must then drop
`offer_valid` before presenting the next envelope. An internal `offer_seen` latch prevents a held-high
envelope from being claimed twice.

If every worker is busy, `offer_blocked` remains asserted. The unseen envelope stays pending and is
claimed automatically when a worker becomes idle.

### Worker state

Each worker stores exactly three whole vectors:

```text
held recipe
held reservation
held promise
```

A nonempty promise means the worker is busy. While its held recipe is nonempty, the controller drives
that recipe and the one-craft requester demand into `AssemblerDevice`. When `working=1` is observed,
the recipe/request demand are withdrawn. The validated Factorio Set-recipe behavior lets the active
craft finish while preventing a second craft from starting. Reservation and promise remain held until
`working` later returns to zero.

The pool publishes additive whole-vector buses:

```text
reserved = sum(worker reservations)
promised = sum(worker promises)
```

A newly claimed envelope appears on these buses immediately in the claim reaction and then from held
worker state, so central accounting has no dispatch gap.

Adding workers changes worker-local state and one `working` observation per worker. The shared job
envelope stays constant-size and does not depend on the number of item or recipe prototypes.

### Generate the physical probe

The default generator compiles the controller and attaches real `AssemblerDevice` instances using the
typed exact-overlap anchor ABI:

```bash
uv run python -m examples.autonomous_mall.worker_pool --workers 2 > mall-workers.txt
```

For the controller alone:

```bash
uv run python -m examples.autonomous_mall.worker_pool \
  --workers 2 \
  --controller-only > mall-worker-controller.txt
```

The worker-pool CLI selects the deterministic `safe-folded-crossbar` physical layout. The compiler
still attempts ordinary combinator packing first. If a packed Level graph creates an abstract net
conflict that cannot be assigned to Factorio's two wire colors, `compile_circuit()` retries the same
optimized semantic module with physical packing disabled. Intrinsically non-two-colorable unpacked
graphs still fail normally; this fallback only prevents an optional packing transform from making an
otherwise realizable circuit uncompilable.

The composed probe deliberately leaves the external job-envelope and aggregate-ledger anchors exposed
for manual wiring. Unused worker observation ports also remain available for inspection.

## Retained quality oracle

The older offline quality oracle remains useful for a later milestone, but its economic conventions
are separate from the simplified deterministic scheduler/worker pool.

- `factorio_data.py` extracts a conservative deterministic item-recipe subset from a real Factorio
  `data-raw-dump.json`;
- `recipe_graph.py` performs canonical recipe selection and DAG construction;
- `quality_mechanics.py` contains exact expected-value helpers for quality and recycling;
- `quality_policy_graph.py` expands the DAG into quality-qualified actions;
- `linear.py` provides the exact rational LP helper;
- `quality_policy.py` computes the older prescribed-raw-material expected-flow optimum.

For example:

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

## What remains unresolved

The worker execution/claim protocol now has a concrete prototype. The next missing layer is the
**circuit-side job former** that turns live mall demand and stock into the four-field offer envelope.
In particular we still need to settle:

- how live roboport stock is combined with worker reservation/promise buses;
- how recipe ingredients and product amounts are discovered/queryable in game without a physical
  structure that scales with the recipe database;
- how recursive missing-intermediate demand is represented compactly;
- how repeated one-craft offers are paced and whether a later explicit batch escrow protocol is worth
  the extra worker complexity;
- how quality/productivity worker roles extend this deterministic core later.

Before physical-size reasoning, read `docs/factorio-2-circuit-mechanics.md`. Factorio 2.x constant
combinators are whole-vector sources; the legacy "20 values per constant combinator" assumption must
not be used.

## Validation

Focused deterministic routine tests:

```bash
uv run pytest \
  tests/examples/autonomous_mall/test_scheduler.py \
  tests/examples/autonomous_mall/test_worker_pool.py \
  tests/examples/autonomous_mall/test_worker_pool_compile_probe.py
```

The worker-pool tests cover held-valid deduplication, first-free assignment, blocked-offer retention,
recipe withdrawal after craft start, reservation/promise release, packed/unpacked wire-color
regressions, compiler fallback orchestration, and abstract-physical lowering. The two-`AssemblerDevice`
composed-blueprint check remains marked `slow`; run it explicitly with marker filtering when desired.

The retained quality-oracle suite is:

```bash
uv run pytest \
  tests/examples/autonomous_mall/test_linear.py \
  tests/examples/autonomous_mall/test_quality_mechanics.py \
  tests/examples/autonomous_mall/test_recipe_graph.py \
  tests/examples/autonomous_mall/test_quality_policy_graph.py \
  tests/examples/autonomous_mall/test_quality_policy.py
```
