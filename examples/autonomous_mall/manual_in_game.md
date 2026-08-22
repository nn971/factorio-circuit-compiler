# Complete tileable autonomous mall in-game test

The complete-cell generator produces pasteable P/Q/R production workers. The normal test path no longer
requires manually wiring requester chests, machines, inserters, completion latches, or duplicating recipe
ingredients into controller constants.

## Generate and import

From the repository root:

```bash
uv sync --extra dev
uv run pytest -m acceptance \
  tests/examples/autonomous_mall/test_manual_controller.py \
  tests/examples/autonomous_mall/test_device_tiles.py
uv run python -m examples.autonomous_mall.complete_controller \
  > autonomous-mall-complete-blueprint.txt
```

Import `autonomous-mall-complete-blueprint.txt` into Factorio.

The book contains:

```text
0  complete row: [HEAD][P0][P1][Q0][Q1][R0]
1  reusable complete P productivity worker
2  reusable complete Q quality worker
3  reusable complete R recycler worker
4  controller-only diagnostic row
5  reusable HEAD tile
```

The complete cells use a 48x72 absolute snapping grid. The upper region is the transaction controller;
the lower bay contains the physical machine and its local circuit plumbing.

## User-facing controls

There are only three kinds of editable command marker in the complete design:

```text
HEAD:  DOCK CONTROL D/L — EDIT HERE
P/Q:   DOCK RECIPE COMMAND — EDIT HERE
R:     DOCK RECYCLE ITEM — EDIT HERE
```

P/Q also expose:

```text
DOCK AUTO ingredients — DO NOT EDIT
```

This is an observation/debug point, not a second configuration input. The assembler is kept on the selected
recipe before dispatch and uses Factorio's `Read ingredients` circuit mode. Its one-craft ingredient vector
is fed automatically into the worker reservation input and therefore into the requester demand.

Do **not** manually enter an ingredient list for P/Q workers.

## Physical worker geometry

A P or Q cell contains:

```text
requester chest -> bulk inserter -> assembling machine 3 -> bulk inserter -> provider chest
```

The two bulk inserters transfer left-to-right. Their blueprint direction is the pickup-facing direction,
so they are configured to pick up from the west and drop east. The input inserter has stack-size override 1.

P workers request four `productivity-module-3`; Q workers request four `quality-module-3`.

The recycler is physically different:

```text
             provider chest
                  ^
                  | direct recycler output
              recycler
                  ^
            bulk inserter
                  ^
            requester chest
```

The recycler is north-facing, uses its native 2x4 footprint, has one south-side input bulk inserter, and
places its output directly into the provider chest. It requests four `quality-module-3`.

## Local device protocol

For P/Q workers the generated device layer does the following automatically:

1. `RECIPE COMMAND` drives both the worker's recipe-presence check and the assembler's Set recipe input.
2. Set recipe is isolated on the assembler's green input network.
3. The assembler's red output network uses `Read ingredients`, `Read working`, and `Read recipe finished`.
4. W/F status lanes are removed from the ingredient vector before it enters `job_request` reservation.
5. The accepted one-craft ingredient vector becomes requester-chest `Set requests`.
6. The input bulk inserter receives item filters from the request and is enabled only in the worker input phase.
7. The recipe-finished pulse is captured by a durable F latch and acknowledged by the worker.

The old compiler-level names `job_recipe` and `job_request` still exist internally, but for a complete P/Q
cell `job_request` is now generated from the assembler itself rather than edited by the player.

For R, `RECYCLE ITEM` remains a direct item/quality request because a recycler chooses its process from the
inserted item rather than from a Set-recipe command.

## External wiring

Power the row normally with poles/substations.

For the basic test there is only one external circuit wire:

1. put a roboport in an isolated logistic network;
2. enable `Read logistic network contents`;
3. red-wire the roboport to HEAD's `DOCK roboport stock` marker.

The horizontal material and control buses are already joined between neighboring tiles.

## HEAD controls

Open the constant combinator labelled:

```text
DOCK CONTROL D/L — EDIT HERE
```

It is preconfigured with two slots:

```text
signal-D = 0
signal-L = 0
```

Change only their counts during the manual test:

```text
IDLE:     D=0, L=0    snapshot follows live roboport stock
FREEZE:   D=1, L=0    snapshot freezes and reservations propagate
RUN:      D=1, L=1    accepted workers execute once
REARM:    D=0, L=0    workers re-arm and snapshot resumes tracking
```

Wait roughly one second in FREEZE while visually testing the prototype.

## Configure an assembler worker

Open P0's constant combinator labelled:

```text
DOCK RECIPE COMMAND — EDIT HERE
```

For the first test set exactly one recipe signal:

```text
iron gear wheel recipe = 1
```

Leave P1, Q0 and Q1 recipe-command combinators empty. While D=0/L=0, wait briefly for the assembler's
`Read ingredients` output to propagate. P0's `AUTO ingredients — DO NOT EDIT` point should then carry the
one-craft iron-plate requirement automatically.

No iron-plate constant needs to be entered by hand.

## Test 1: visual placement

Paste book entry 0 under power and inspect:

```text
[HEAD][ P0 ][ P1 ][ Q0 ][ Q1 ][ R0 ]
```

Check these before functional testing:

- HEAD has the clearly labelled `CONTROL D/L — EDIT HERE` constant;
- every P/Q cell has `RECIPE COMMAND — EDIT HERE` and `AUTO ingredients — DO NOT EDIT`;
- P/Q item flow is requester -> bulk inserter -> assembler -> bulk inserter -> provider;
- the two P/Q inserters point left-to-right;
- R uses a vertical north-facing recycler, one input bulk inserter, and direct output to its provider chest;
- P workers request productivity modules; Q/R workers request quality modules.

## Test 2: automatic recipe ingredients

With D=0/L=0, configure only P0 for the iron-gear recipe.

Expected before dispatch:

```text
assembler recipe = iron gear wheel
AUTO ingredients = 2 normal iron plates
```

The exact displayed recipe signal representation depends on Factorio's recipe-signal UI, but the important
part is that the ingredient vector appears without manual ingredient configuration.

If the recipe is selected but `AUTO ingredients` stays empty, stop here and report that result; this isolates
the new recipe->ingredients feedback path from reservation and machine execution.

## Test 3: one complete P0 transaction

Put at least two normal iron plates in the logistic network. Let HEAD observe them in IDLE, then run:

```text
IDLE    D=0 L=0
FREEZE  D=1 L=0
RUN     D=1 L=1
```

Expected sequence:

```text
P0 accepted
-> requester chest asks for two iron plates
-> robots deliver
-> input bulk inserter feeds the required plates one at a time
-> assembler performs one iron-gear craft
-> output bulk inserter moves the gear to the provider chest
-> recipe-finished latch completes the transaction
-> worker returns idle
```

Keep D=1/L=1 for several seconds after completion. A second craft must **not** start. Then use D=0/L=0 to
re-arm the worker.

## Test 4: P0 + Q0 reservation/concurrency

Configure the same iron-gear recipe on P0 and Q0. Give the frozen stock four iron plates.

After FREEZE, expected:

```text
P0 accepted = 1
Q0 accepted = 1
```

After RUN, both machines should execute concurrently. Live roboport stock may fall while robots transport
items; the frozen reservation decisions must not change.

Repeat with only two plates. Expected:

```text
P0 accepted = 1
Q0 accepted = 0
```

This checks the left-to-right atomic reservation order without manually duplicating ingredient vectors.

## Later tests

After the above succeeds:

- repeated P0 batches should preserve partial productivity-bar progress;
- repeated Q batches should perform one quality attempt per accepted transaction;
- R0 should consume exactly the configured item/quality from `RECYCLE ITEM — EDIT HERE`, including
  zero-output recycler attempts completing through recipe-finished;
- all five workers should retain priority P0, P1, Q0, Q1, R0 while accepted workers execute concurrently;
- holding launch high must never retrigger a worker; returning D/L to zero re-arms the row.

## Scope

The complete generated cells currently target ordinary no-fluid assembler recipes and one-item recycler
transactions. The economic planner is still the Python oracle; automatic job selection and automatic D/L
batch scheduling are later controller layers. This milestone is specifically validating that physical workers
are now pasteable, self-wired, and recipe-driven rather than requiring duplicate manual configuration.
