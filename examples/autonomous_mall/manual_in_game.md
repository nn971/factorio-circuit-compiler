# Manual in-game autonomous mall test

The first physical prototype is deliberately modular. The previous monolithic controller lowered to an
abstract topology whose runtime-open vector nets could not be assigned to only Factorio's two wire colors.
The new version compiles four small templates and composes them by manual wires in game.

The physical worker pool is:

```text
p0, p1  productivity assemblers
q0, q1  quality assemblers
r0      quality recycler
```

The module roles are fixed. Do not swap modules dynamically.

## Generate the blueprint book

From the repository root:

```bash
uv sync --extra dev
uv run pytest tests/examples/autonomous_mall/test_manual_controller.py
uv run pytest -m acceptance tests/examples/autonomous_mall/test_manual_controller.py
uv run python -m examples.autonomous_mall.manual_controller \
  > autonomous-mall-manual-blueprint.txt
```

The last command prints one importable blueprint-book string to stdout and a wiring map to stderr. The
book contains four templates:

```text
stock snapshot                    paste 1
reservation cell                  paste 5
assembler worker                  paste 4
recycler worker                   paste 1
```

Use the printed wire colors for every compiled input/output marker.

## Two manual batch controls

The prototype uses two persistent scalar controls:

```text
dispatch = 0/1
launch   = 0/1
```

A batch has four phases:

```text
IDLE:     dispatch=0, launch=0
FREEZE:   dispatch=1, launch=0
RUN:      dispatch=1, launch=1
REARM:    dispatch=0, launch=0
```

Keep `dispatch=0` for a while before a batch. The stock-snapshot cell continuously tracks the roboport
while dispatch is low. Raising dispatch freezes that snapshot and activates the reservation chain.

Do not raise launch immediately. Wait until the five reservation cells have visibly settled; one second is
ample for this first manual test. Then raise launch. Each worker cell starts at most once while its
`accepted` signal remains high. After all accepted workers finish and their completion latches clear,
return launch and dispatch to zero. The worker cells then re-arm for the next batch and the stock snapshot
resumes tracking live inventory.

This two-phase handshake is intentionally manual. It avoids making any assumption about clock phase across
independently compiled blueprints. A later external-device protocol can automate it.

## Stock snapshot

Place one roboport in an isolated logistic network and enable **Read logistic network contents**.

Wire:

```text
roboport contents -> snapshot INPUT stock
dispatch          -> snapshot INPUT dispatch
```

The cell exposes:

```text
OUTPUT snapshot
OUTPUT frozen
```

Use `OUTPUT snapshot`, not the live roboport wire, as the input of the first reservation cell.

## Reservation chain

Paste five copies of the reservation-cell template and label them:

```text
p0 -> p1 -> q0 -> q1 -> r0
```

Every cell has:

```text
INPUT active
INPUT job_enable
INPUT available
INPUT job_request

OUTPUT accepted
OUTPUT remaining
```

Wire the chain:

```text
stock snapshot OUTPUT snapshot -> p0 INPUT available
p0 OUTPUT remaining            -> p1 INPUT available
p1 OUTPUT remaining            -> q0 INPUT available
q0 OUTPUT remaining            -> q1 INPUT available
q1 OUTPUT remaining            -> r0 INPUT available
```

Wire the same `dispatch` signal to every `INPUT active`.

For each worker, one constant-combinator job definition drives both its reservation cell and its worker
cell:

```text
job_enable  scalar 1 when this candidate is enabled
job_request exact ingredients/items for one physical attempt
job_recipe  assembler product/recipe signal; worker cell only
```

The reservation cell computes:

```text
accepted = active
           AND job_enable
           AND request is nonempty
           AND job_request <= available lane-by-lane

remaining = available - job_request   if accepted
            available                 otherwise
```

Because the five cells are physically chained, `q0` cannot spend material already reserved by p0 or p1.
The chain is evaluated against the frozen stock snapshot, so later roboport changes caused by flying robots
cannot create a duplicate reservation.

## Worker cells

Paste four assembler-worker cells and one recycler-worker cell.

Every worker cell receives:

```text
INPUT accepted    from its reservation cell
INPUT launch      shared manual launch signal
INPUT working     machine Read-working signal
INPUT finished    durable completion latch
INPUT job_request same request vector used by its reservation cell
```

Assembler workers also receive:

```text
INPUT job_recipe
```

Outputs are:

```text
OUTPUT requester_demand
OUTPUT input_enable
OUTPUT busy
OUTPUT waiting_finished
OUTPUT ack_finished
OUTPUT armed
```

Assembler workers additionally expose:

```text
OUTPUT recipe
```

The worker latches its request/recipe only when `launch=1`, `accepted=1`, it is idle, and it has not
already consumed this accepted batch. Keeping accepted and launch high cannot retrigger the job. The worker
re-arms only after accepted returns to zero, which happens when dispatch returns low.

## Assembler external device

For p0, p1, q0, q1 build:

```text
requester chest -> stack-size-1 inserter -> assembling machine 3 -> inserter -> provider chest
```

Connect:

```text
worker OUTPUT requester_demand -> requester chest Set requests
worker OUTPUT recipe           -> assembler Set recipe
assembler Read working         -> worker INPUT working
completion latch               -> worker INPUT finished
worker OUTPUT ack_finished     -> completion-latch reset
```

Install productivity modules permanently in p0/p1 and quality modules permanently in q0/q1.

The recipe output intentionally stays latched between transactions. This preserves partial productivity
progress when the same productivity worker repeats the same recipe.

### Exact one-craft input feeder

Do not stop a job by removing its recipe. Instead, starve the assembler after one craft begins.

For the iron-gear smoke test, set the input inserter stack-size override to 1 and enable it only when:

```text
worker OUTPUT input_enable > 0
AND
assembler Read working = 0
```

The assembler's working signal should reach this inserter locally, without passing through another
compiled controller first.

For a general no-fluid multi-ingredient recipe, configure the assembler to **Read contents** on a separate
wire and build:

```text
missing_to_machine = positive(worker requester_demand - machine_contents)
```

Use `missing_to_machine` to Set filters on the stack-size-1 input inserter. Keep the same enable condition
`input_enable > 0 AND working = 0`.

This prevents one ingredient from buffering several crafts while another ingredient is still in transit.
Keep catalyst-style recipes out of this first test because machine contents is ambiguous when an item is
both an ingredient and product; a later reusable worker protocol should count inserter hand pulses instead.

## Durable completion latch

Do not connect a one-tick Read-recipe-finished pulse directly to `INPUT finished`.

Let:

```text
S = machine Read recipe finished pulse
L = concrete scalar used by worker INPUT finished
A = concrete scalar used by worker OUTPUT ack_finished
```

Use two deciders:

```text
SET:   if S > 0           -> L = 1
HOLD:  if L > 0 AND A = 0 -> L = 1
```

Feed HOLD back to itself, feed SET into HOLD, connect HOLD to worker `INPUT finished`, and connect worker
`OUTPUT ack_finished` to the HOLD condition network.

A recycler also uses this latch. A recycle may produce zero output, so completion must be driven by the
machine-finished signal rather than by observing an output item.

## Recycler external device

For r0 build:

```text
requester chest -> stack-size-1 inserter -> recycler -> output collection
```

Connect `requester_demand`, `input_enable`, Read-working, the completion latch, and ack exactly as above.
There is no recipe signal for the recycler. Install quality modules permanently.

## Test 1: compile all four templates

Run:

```bash
uv run pytest -m acceptance tests/examples/autonomous_mall/test_manual_controller.py
```

Expected: four passing parametrized compile cases. This is the regression for the previous wire-color
failure.

## Test 2: one-shot p0

Configure only p0:

```text
job_enable  = 1
job_request = normal iron plate x2
job_recipe  = normal iron gear wheel x1
```

Put at least two normal plates in logistics.

1. Keep dispatch=0 and launch=0 until snapshot follows the roboport.
2. Set dispatch=1.
3. Wait for the chain to settle; p0 accepted should be 1.
4. Set launch=1.

Expected: p0 requests two plates, feeds exactly one craft, starts, finishes exactly once, and acknowledges
the completion latch. Keeping dispatch and launch high must not start a second craft.

After completion set launch=0 and dispatch=0. `accepted` drops and `armed` returns high.

## Test 3: atomic reservation

Configure p0 and q0 as identical one-gear jobs, each requesting two plates.

With exactly two plates in the frozen stock snapshot:

```text
p0 accepted = 1
q0 accepted = 0
```

With four plates:

```text
p0 accepted = 1
q0 accepted = 1
```

Do not raise launch until these accepted values have settled. With four plates, raising launch should let
p0 and q0 run concurrently.

This is the primary multi-worker reservation and anti-oscillation test.

## Test 4: productivity persistence

Keep p0 on iron gears with productivity modules. Run repeated batches without changing job_recipe.

Expected: partial productivity-bar progress survives between transactions and eventually produces bonus
output. If it resets every batch, the assembler recipe wiring is wrong.

## Test 5: quality production

Configure q0 for one normal iron-gear craft with quality modules. Run repeated batches.

Expected: each accepted batch causes exactly one craft; quality varies stochastically in real inventory.

## Test 6: multi-ingredient recipe

Add the `missing_to_machine` feeder circuit and choose a no-fluid recipe with at least two ingredient
types. Set job_request to exactly one craft's ingredient vector.

Expected: each ingredient lane stops being fed at its requested count, and only one craft starts.

## Test 7: recycler

Configure r0:

```text
job_enable  = 1
job_request = one gear at the exact quality to recycle
```

Expected: one item is consumed per launched accepted batch. Zero-output recycle attempts still finish and
acknowledge correctly.

## Test 8: all five workers

Give every worker an affordable request. Freeze the stock with dispatch, wait for the full chain to settle,
then raise launch.

Expected:

- reservation priority is p0, p1, q0, q1, r0;
- each downstream cell sees upstream reservations already subtracted;
- all accepted workers may run concurrently after launch;
- holding launch high cannot repeat a worker;
- lowering dispatch re-arms every worker for the next batch.

## Current boundary

This validates the physical transaction contract: frozen roboport stock, atomic chained reservations,
requester-chest transport, fixed productivity/quality/recycler roles, exact one-craft feeding for ordinary
no-fluid recipes, productivity-progress preservation, durable completion, and multi-worker operation
without robot-flight feedback oscillation.

The material-efficiency LP in `planner.py` is still the Python oracle. Job constant combinators stand in for
the future circuit-side economic planner. The next milestone is to automate job selection and the
`dispatch -> settle -> launch` handshake without reintroducing physically impossible open-vector fan-in.
