# Snap-together in-game autonomous mall test

The physical transaction prototype is now built from **grid-snapped tiles with a stable electrical ABI**.
You no longer wire the reservation chain by hand.

The generated blueprint book contains:

```text
0  complete controller: [HEAD][P0][P1][Q0][Q1][R0]
1  reusable HEAD tile
2  reusable ASSEMBLER worker tile
3  reusable RECYCLER worker tile
```

Each tile is 48x48 and uses absolute snap-to-grid. Horizontal ports terminate in blank 1x1 constant
combinators on the tile boundary. Adjacent tiles put the matching connectors on the exact same world tile.
Pasting the next tile over that existing marker adds its wires while retaining the previous wires, so the
shared marker becomes the plug/socket between modules.

The internal compiler is free to choose red/green networks and virtual scalar lanes. A small arithmetic
adapter strip hides those choices from the public dock:

- every external dock uses **red wire**;
- whole-vector ports use `EACH * 1 -> EACH` isolation;
- machine scalar ports are renamed onto fixed mall protocol signals.

This is why separately compiled tiles can be composed safely without inspecting a generated wiring map.

## Generate and import

From the repository root:

```bash
uv sync --extra dev
uv run pytest tests/synthesis/test_module_interface.py \
  tests/examples/autonomous_mall/test_manual_controller.py
uv run pytest -m acceptance \
  tests/examples/autonomous_mall/test_manual_controller.py
uv run python -m examples.autonomous_mall.manual_controller \
  > autonomous-mall-manual-blueprint.txt
```

Import `autonomous-mall-manual-blueprint.txt` into Factorio. Start with book entry **0**, the fully assembled
six-tile controller. Entries 1-3 are useful for verifying that manual tile-by-tile stamping also works.

## What is already connected

The assembled controller is:

```text
                 frozen available-material bus
          ------------------------------------------------>

 [ HEAD ][ P0 ][ P1 ][ Q0 ][ Q1 ][ R0 ]
          ------------------------------------------------>
                       control bus
```

There are two shared horizontal boundary docks between every adjacent pair:

```text
available bus   arbitrary item/quality signal vector
control bus     fixed mall control lanes
```

The worker priority is therefore physically encoded by the left-to-right order:

```text
P0 -> P1 -> Q0 -> Q1 -> R0
```

Every worker computes

```text
accepted = dispatch
           AND job_request is nonempty
           AND the entire request fits in available_in
           AND job_recipe is nonempty          # assembler tiles only

remaining_out = available_in - job_request     # only when accepted
```

`remaining_out` is the next tile's `available_in`. A downstream worker therefore cannot spend material
already reserved by an upstream worker.

## HEAD tile

HEAD has two things you need to touch.

### Roboport stock dock

The bottom marker labelled:

```text
DOCK roboport stock
```

is the only material-stock connection. Put one roboport in an isolated logistic network, enable **Read
logistic network contents**, and connect the roboport to this dock with a **red wire**.

### Control marker

HEAD's `INPUT control` marker is intentionally left editable instead of being given another external dock.
Use the following fixed virtual signals:

```text
signal-D = dispatch
signal-L = launch
```

A batch uses four manual phases:

```text
IDLE:     D=0, L=0
FREEZE:   D=1, L=0
RUN:      D=1, L=1
REARM:    D=0, L=0
```

While `D=0`, HEAD continuously tracks live roboport stock. Raising `D` freezes the snapshot. Wait roughly
one second during the first tests so the five reservation stages visibly settle, then raise `L`.

## Configure jobs directly on each tile

There is no longer a separate job-definition constant combinator or `job_enable` wire.

The marker labelled `INPUT job_request` is itself an editable constant combinator. Put the exact
one-attempt ingredient vector there. An empty request disables that tile.

Assembler tiles also have `INPUT job_recipe`; put the exact recipe/product signal there. A nonempty request
with an empty recipe is not accepted.

Example for one normal iron gear:

```text
job_request:  normal iron plate x2
job_recipe:   normal iron gear wheel x1
```

Configure P0/P1 for productivity-module machines, Q0/Q1 for quality-module machines, and R0 for the
recycler. The controller logic for the four assemblers is identical; their physical module role lives in
the attached machine.

## Stable machine-side ABI

Every worker exposes its physical device interface along the bottom edge. All these docks use **red wire**.
Vector docks preserve the item/quality vector directly; scalar docks use fixed virtual lanes:

```text
DOCK requester demand       vector
DOCK recipe                 vector, assembler tiles only
DOCK input enable           signal-I
DOCK working                signal-W
DOCK finished               signal-F
DOCK finish acknowledgement signal-A
```

This means a future assembler/recycler device blueprint can use matching top-edge markers and be pasted
straight underneath a worker tile with the same overlap trick. For this milestone, wire the physical
machines to these stable docks manually; no compiler-generated signal lookup is needed.

## First assembler device

For P0 build:

```text
requester chest -> stack-size-1 inserter -> assembling machine 3 -> inserter -> provider chest
```

Connect with red wire:

```text
DOCK requester demand -> requester chest Set requests
DOCK recipe           -> assembler Set recipe
assembler Read working as signal-W -> DOCK working
completion latch signal-F          -> DOCK finished
DOCK finish acknowledgement signal-A -> completion-latch reset
```

Install productivity modules permanently in the P worker. Q workers use quality modules permanently.

### Exact one-craft feeder

Do not stop a productivity job by removing its recipe; that can discard partial productivity progress.
Keep the recipe selected and starve the machine after one craft starts.

For the iron-gear smoke test, set the input inserter stack-size override to 1 and enable it only when:

```text
signal-I > 0
AND
assembler Read working = 0
```

The assembler's local working signal should gate the inserter directly. The two plates enter, the craft
starts, working rises, and no third plate is inserted.

For a general no-fluid multi-ingredient recipe, later add:

```text
missing_to_machine = positive(requester_demand - machine_contents)
```

and use that vector to Set filters on the stack-size-1 input inserter. Keep catalyst-style recipes out of
this first test because machine contents is ambiguous when an item is simultaneously ingredient and
product.

## Durable completion latch

Do not feed a one-tick recipe-finished pulse directly to the worker. Convert it to the fixed `signal-F`
protocol lane:

```text
SET:   if recipe_finished > 0          -> signal-F = 1
HOLD:  if signal-F > 0 AND signal-A=0  -> signal-F = 1
```

Feed HOLD back to itself. Connect the held `signal-F` to `DOCK finished`; connect `DOCK finish
acknowledgement` (`signal-A`) back to the HOLD condition.

The recycler must use the same latch because a recycle attempt may legitimately produce zero output.

## Test 1: verify tile docking only

Before attaching machines, import the **ASSEMBLER worker tile** entry and stamp several copies in one row
using the blueprint's absolute grid snapping.

At every seam you should see **one**, not two displaced, boundary constant combinators at the available-bus
and control-bus heights. Hover them: each shared marker should have red wires into both neighboring tiles.

This visually checks the exact-overlap plug/socket mechanism.

## Test 2: stock snapshot and reservation chain

Use the complete controller blueprint. Put exactly two normal iron plates in the logistic network.
Configure:

```text
P0 job_request = 2 iron plates
P0 job_recipe  = 1 iron gear
Q0 job_request = 2 iron plates
Q0 job_recipe  = 1 iron gear
```

Leave P1/Q1/R0 requests empty.

Set:

```text
D=1, L=0
```

After the chain settles, expected:

```text
P0 accepted = 1
Q0 accepted = 0
```

Repeat from IDLE with four plates. Expected:

```text
P0 accepted = 1
Q0 accepted = 1
```

No horizontal wiring should be touched during this test.

## Test 3: one-shot P0

With at least two plates and only P0 enabled:

1. `D=0, L=0`: let HEAD follow roboport inventory.
2. `D=1, L=0`: freeze and wait for P0 acceptance.
3. `D=1, L=1`: launch.

Expected:

1. requester demand asks for exactly two plates;
2. the gear recipe remains selected;
3. the feeder inserts exactly two plates;
4. working rises and blocks the feeder locally;
5. exactly one gear finishes;
6. `signal-F` latches;
7. `signal-A` acknowledges it;
8. the worker returns idle.

Keep both D and L high for several seconds after completion. A second craft must **not** start.

Then set `D=0, L=0` to re-arm the worker.

## Test 4: parallel reservation/execution

With four plates frozen and identical one-gear jobs on P0 and Q0, both should be accepted. Raising launch
should let the two machines run concurrently. Roboport stock changing while robots fly must not alter the
already frozen reservations.

## Test 5: productivity persistence

Keep P0 on the same gear recipe and run repeated one-craft batches with productivity modules.

Expected: partial productivity-bar progress survives between batches and eventually produces bonus output.
If it resets every transaction, the machine-side recipe wiring is wrong.

## Test 6: quality

Attach a Q worker with quality modules and repeatedly launch one normal gear attempt.

Expected: every accepted batch still performs exactly one craft, while observed output quality varies.

## Test 7: recycler

Configure R0 `job_request` to one exact-quality gear. There is no recipe dock or job-recipe marker for R0.
Attach a recycler with quality modules.

Expected: one item is consumed per accepted launch. Zero-output attempts still complete through the
`signal-F` / `signal-A` completion protocol.

## Test 8: all five workers

Give all workers affordable jobs, freeze the stock, wait for reservation propagation, then launch.

Expected:

- reservation order is P0, P1, Q0, Q1, R0;
- each tile sees all upstream reservations already subtracted;
- all accepted workers can operate concurrently after launch;
- holding launch high never repeats a transaction;
- returning D and L to zero re-arms the row.

## What this milestone validates

The snap-together controller validates a stronger physical contract than the earlier manual prototype:

- named compiler I/O anchors;
- reusable absolute-grid module geometry;
- exact overlapping plug/socket markers;
- a fixed-red whole-vector ABI across independently synthesized modules;
- fixed scalar machine protocol lanes independent of compiler signal allocation;
- frozen roboport stock and left-to-right atomic reservations;
- integrated reservation + worker FSM tiles;
- requester-chest transport, persistent recipes, durable completion, and one-shot execution.

The material-efficiency LP in `planner.py` is still the Python oracle. Job markers are still configured
manually. The next controller milestone can automate economic job selection and the dispatch/launch
handshake on top of this stable physical module ABI.
