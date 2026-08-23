# Autonomous mall research scaffold

This directory is a research area rather than an accepted final circuit architecture. Earlier
manual worker rows, compiled policy ROMs, and sequential scanners were exploratory implementations
and should not be treated as architectural precedent.

The retained pieces now form four deliberately separate layers:

- `scheduler.py`: an offline deterministic, quality-free multi-worker scheduling oracle;
- `worker_pool.py`: the compact monolithic semantic reference for anonymous worker execution;
- `seamed_worker_pool.py`: the preferred physical worker-pool prototype built from constrained
  components and exact seams;
- the older quality/recycling modules: an offline material-efficiency oracle for later milestones.

Generic `AssemblerDevice`, exact-overlap anchors, constrained component seams, and compiler port
pinning live under `src/factorio_circuit/` and remain reusable compiler infrastructure.

## Deterministic scheduler

`scheduler.py` models the simplified milestone where recipes are deterministic and quality is
irrelevant. It contains no raw-material objective and no configured raw-material boundary.

Its inputs are a target-stock vector, observed physical stock, already-active jobs, and a
deterministic `RecipeCatalog`. Items with no supported producer are inferred to be external inputs.
Multiple producers use the catalog's canonical selection/override rule.

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

## One-craft worker semantics

Both worker implementations use the same first physical contract: one accepted envelope equals
**one craft**.

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

Each worker stores exactly three whole vectors:

```text
held recipe
held reservation
held promise
```

A nonempty promise means the worker is busy. While its held recipe is nonempty, the controller drives
that recipe and the one-craft requester demand into `AssemblerDevice`. When `working=1` is observed,
the recipe/request demand are withdrawn. The validated Factorio Set-recipe behavior lets the active
craft finish while preventing a second craft from starting. Reservation and promise remain held
until `working` later returns to zero.

Workers contribute additive whole-vector ledgers:

```text
reserved = sum(worker reservations)
promised = sum(worker promises)
```

The item/recipe database therefore does not determine the number of physical ROM combinators.

## Preferred physical prototype: constrained seams

`seamed_worker_pool.py` regenerates the worker pool around the constrained component ABI instead of
placing one giant controller and manually routing to N devices.

Its physical topology is regular:

```text
            external offer / ledgers
                     │
                ┌────▼────┐
                │  HEAD   │
                └────┬────┘
                     │  fixed 10-lane bus
             ┌───────▼────────┐     ┌───────────────┐
             │ worker control │────▶│ AssemblerDevice│
             └───────┬────────┘     └───────────────┘
                     │
             ┌───────▼────────┐     ┌───────────────┐
             │ worker control │────▶│ AssemblerDevice│
             └───────┬────────┘     └───────────────┘
                     │
                    ...
                     │
                ┌────▼────┐
                │  TAIL   │
                └─────────┘
```

Every head, worker controller, assembler attachment, and tail owns a declared rectangular footprint.
North/south bus terminals are derived from stable side/slot coordinates. Repetition uses
`compose_component_seams(...)`; callers no longer choose arbitrary translation offsets or relay
waypoints between cells.

### Mall bus

The bus has ten fixed lanes:

```text
forward:  offer_valid, offer_recipe, offer_inputs, offer_product
reverse:  blocked, accepted, busy_count, completion_count, reserved, promised
```

The bus width stays fixed as workers are added. Each repeated worker contributes only its local state,
controller implementation, and one assembler device.

### Stop-and-wait claim protocol

A physically tiled bus has propagation latency. Therefore the new prototype does **not** hold a
combinational first-free claim token high while waiting for a distant acknowledgement.

The dispatch head sends one probe token at a time:

1. a busy worker forwards the probe;
2. the first idle worker consumes it and sends `accepted` back toward the head;
3. if every worker is busy, the probe reaches the tail, which returns `blocked`;
4. the head waits for one of those responses before it may retry the still-held external offer.

This makes the important physical invariant explicit: at most one probe for an offer is in flight, so
a delayed acknowledgement cannot cause two workers to claim the same held envelope.

### Controller / assembler seam

Each worker controller has one four-lane east seam:

```text
recipe
 enable
working
requester_demand
```

The first three commands/observations needed by the mall already live near the west side of
`AssemblerDevice`, except `working`. The mall adapter therefore adds a short, device-owned working
relay path *inside the assembler footprint* and exposes a coherent west seam. It does not route a
wire around the outside of the whole worker as the old probe did. Other assembler observation ports
remain private to the physical device for this milestone.

### Generate the blueprint

Generate the preferred two-worker prototype with:

```bash
uv run python -m examples.autonomous_mall.seamed_worker_pool --workers 2 > mall-workers.txt
```

Change `--workers` to tile more identical worker cells.

For comparison, the older monolithic semantic/physical reference is still available as:

```bash
uv run python -m examples.autonomous_mall.worker_pool --workers 2 > mall-workers-legacy.txt
```

That older generator deliberately remains available while the constrained version is being probed in
game, but it should not be used as the physical-layout precedent for later mall components.

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

The worker execution/claim protocol now has both a compact semantic reference and a constrained
physical prototype. The next missing application layer is the **circuit-side job former** that turns
live mall demand and stock into the four-field offer envelope. In particular we still need to settle:

- how live roboport stock is combined with worker reservation/promise buses;
- how recipe ingredients and product amounts are discovered/queryable in game without a physical
  structure that scales with the recipe database;
- how recursive missing-intermediate demand is represented compactly;
- how repeated one-craft offers are paced and whether a later explicit batch escrow protocol is worth
  the extra worker complexity;
- how quality/productivity worker roles extend this deterministic core later.

At the synthesis layer, constrained components currently pin public docks before annealing and reject
entities outside their declared footprints afterward. A hard placement region consumed directly by
the annealer remains a separate compiler improvement.

Before physical-size reasoning, read `docs/factorio-2-circuit-mechanics.md`. Factorio 2.x constant
combinators are whole-vector sources; the legacy "20 values per constant combinator" assumption must
not be used.

## Validation

Focused deterministic routine tests:

```bash
uv run pytest \
  tests/examples/autonomous_mall/test_scheduler.py \
  tests/examples/autonomous_mall/test_worker_pool.py \
  tests/examples/autonomous_mall/test_seamed_worker_pool.py \
  tests/examples/autonomous_mall/test_worker_pool_compile_probe.py
```

The seamed-worker tests cover stop-and-wait probe/retry behavior, accepted-offer suppression, and
worker consume/forward semantics. Its two-worker full physical construction check is marked `slow`;
run it explicitly when validating changes to physical composition.

The retained quality-oracle suite is:

```bash
uv run pytest \
  tests/examples/autonomous_mall/test_linear.py \
  tests/examples/autonomous_mall/test_quality_mechanics.py \
  tests/examples/autonomous_mall/test_recipe_graph.py \
  tests/examples/autonomous_mall/test_quality_policy_graph.py \
  tests/examples/autonomous_mall/test_quality_policy.py
```
