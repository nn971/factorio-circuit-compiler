# Complete tileable autonomous mall in-game test

The physical transaction prototype now has **complete pasteable production cells**. The controller-only
tiles remain available for diagnostics, but the normal in-game path no longer requires wiring requester
chests, assemblers, inserters, or completion latches by hand.

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
4  controller-only [HEAD][P0][P1][Q0][Q1][R0] diagnostic row
5  reusable HEAD tile
```

The complete worker cells use a 48x72 absolute snapping grid. Their upper 48 tiles are the already-tested
controller. The lower bay contains the physical machine and all local circuit plumbing.

## What a complete worker contains

A P or Q cell contains:

```text
requester chest -> stack inserter -> assembling machine 3 -> stack inserter -> provider chest
```

The P machine requests four `productivity-module-3`; the Q machine requests four `quality-module-3`.
The R cell substitutes a recycler with four `quality-module-3`.

The generator also wires the local device protocol automatically:

- requester demand drives requester-chest `Set requests`;
- assembler recipe is isolated onto the machine's green input network;
- machine contents, working, and recipe-finished status use its red output network;
- the input inserter uses stack-size override 1;
- its circuit filters are `positive(requester_demand - machine_contents)`;
- it is enabled only while the worker is in its input phase and the machine is not working;
- the one-tick recipe-finished signal is caught by a SET/HOLD latch;
- the worker acknowledgement clears that latch.

Consequently the machine-side `signal-I/W/F/A` ABI still exists internally for debugging, but the player
no longer wires it.

### Current recipe scope

The generated feeder supports ordinary **solid-only, no-fluid** recipes with any number of ingredients.
For now exclude recipes where the same item is simultaneously an ingredient and a product (catalyst-like
recipes), because `Read contents` does not identify which semantic role an item occupies.

Productivity workers must of course use recipes that permit productivity modules.

## What remains external

The generator deliberately does not guess your electric-grid layout. Paste the row under substation/pole
coverage so every combinator, inserter, logistic chest, and machine has power.

There is only one external **circuit wire** required for the basic test:

1. put a roboport in an isolated logistic network;
2. enable `Read logistic network contents`;
3. red-wire the roboport to HEAD's `DOCK roboport stock` marker.

The horizontal available/control buses are already joined across every worker seam.

## HEAD controls

HEAD's editable `INPUT control` marker uses:

```text
signal-D = dispatch
signal-L = launch
```

Use four manual phases for the first tests:

```text
IDLE:     D=0, L=0    snapshot follows live roboport stock
FREEZE:   D=1, L=0    snapshot freezes and reservations propagate
RUN:      D=1, L=1    accepted workers execute once
REARM:    D=0, L=0    workers re-arm and snapshot resumes tracking
```

Wait roughly one second in FREEZE while visually testing the prototype. The final controller can automate
this settle/launch handshake later.

## Configure a worker

Each worker has editable controller markers rather than external job wiring.

For an assembler worker set:

```text
INPUT job_request = exact one-craft ingredient vector
INPUT job_recipe  = recipe/product signal x1
```

An empty `job_request` disables that worker. An assembler request with empty `job_recipe` is also rejected.

For one normal iron gear:

```text
job_request:  normal iron plate x2
job_recipe:   normal iron gear wheel x1
```

R0 has no recipe marker; configure only the exact-quality item to recycle in `job_request`.

## Test 1: complete-cell placement

Paste book entry 0 under power coverage. Before changing any controls, inspect the row:

```text
[HEAD][ P0 ][ P1 ][ Q0 ][ Q1 ][ R0 ]
```

Every worker should already contain a requester chest, two inserters, its machine, and a provider chest.
P0/P1 should request productivity modules; Q0/Q1 and R0 should request quality modules.

At each horizontal seam there should still be one shared `available bus` marker and one shared `control
bus` marker, each with red wiring into both neighboring controllers.

If the physical machine entities fail to import but entry 4 still imports correctly, report exactly which
entities/settings Factorio rejected; entry 4 isolates the known-good controller from the new device layer.

## Test 2: reservation only

Leave all machines idle. Put exactly two normal iron plates in the logistic network. Configure identical
one-gear jobs on P0 and Q0; leave P1/Q1/R0 empty.

From IDLE, raise D only:

```text
D=1, L=0
```

Expected after reservation propagation:

```text
P0 accepted = 1
Q0 accepted = 0
```

Return to IDLE, put four plates in logistics, let HEAD observe them, then freeze again. Expected:

```text
P0 accepted = 1
Q0 accepted = 1
```

This checks the shared horizontal material bus and left-to-right atomic reservation order.

## Test 3: one complete P0 transaction

Enable only P0 with the iron-gear job and keep at least two iron plates in logistics.

Run:

```text
IDLE    D=0 L=0
FREEZE  D=1 L=0
RUN     D=1 L=1
```

Expected physical sequence:

```text
P0 is accepted
-> requester chest asks for two plates
-> robots deliver the plates
-> generated feeder inserts missing ingredients one at a time
-> assembler starts
-> working status blocks further feeding
-> exactly one gear craft completes
-> generated completion latch captures recipe-finished
-> worker acknowledges completion
-> latch clears and worker returns idle
```

Keep `D=1, L=1` for several seconds after the gear finishes. **A second craft must not start.**

Then set `D=0, L=0`. The worker should re-arm for another transaction.

## Test 4: P0 and Q0 concurrently

Configure the same gear job on P0 and Q0 and give the frozen snapshot four plates.

Expected:

```text
P0 accepted = 1
Q0 accepted = 1
```

Raise launch. Both machines should operate concurrently. Robot flight and the resulting change in live
logistic inventory must not alter the already-frozen reservation decisions.

P0 should use productivity modules and Q0 quality modules without either worker changing role.

## Test 5: productivity persistence

Run repeated one-craft P0 batches without changing the recipe.

Expected: partial productivity-bar progress survives between transactions and eventually yields the
productivity bonus. The worker stops a transaction by starving the machine, not by clearing its recipe.

## Test 6: quality

Run repeated Q0 batches from normal ingredients.

Expected: each accepted transaction still performs one attempt, while output quality follows the game's
quality randomness. Actual outputs return to logistic stock and are observed by later replanning/snapshots;
the controller never assumes expected quality output physically exists.

## Test 7: recycler

Configure R0 to request one exact-quality recyclable item and launch it through the same batch protocol.
The recycler selects its reverse process from the inserted item; it has no Set-recipe connection.

Expected: exactly one item is consumed per accepted launch. Even a recycle attempt that yields no physical
output still completes because completion is based on the machine's recipe-finished pulse, not on detected
output items.

## Test 8: all five workers

Give all five workers affordable jobs, freeze stock, wait for reservation propagation, then launch.

Expected:

- priority is P0, P1, Q0, Q1, R0;
- each worker sees upstream reservations already subtracted;
- all accepted workers can run concurrently;
- holding launch high never starts a second transaction;
- returning D and L to zero re-arms the complete row.

## What this milestone validates

This version validates the full physical transaction layer rather than only its controller:

- named compiler I/O anchors and snap-compatible module geometry;
- fixed-red horizontal whole-vector ABI;
- frozen roboport inventory and atomic left-to-right reservations;
- integrated reservation and one-shot worker FSM;
- generated requester-chest transport;
- generated general solid-ingredient feeder;
- persistent assembler recipes;
- fixed productivity/quality physical roles;
- generated durable completion latch;
- complete P/Q/R cells that can be stamped as reusable 48x72 units.

The economic planner is still the Python oracle. Job selection and the D/L batch handshake remain manual;
those are the next controller-side automation layers once these complete physical cells pass in-game tests.
