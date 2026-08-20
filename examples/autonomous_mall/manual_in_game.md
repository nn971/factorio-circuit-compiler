# Manual in-game autonomous mall test

This test exercises the transaction layer before the final circuit-side economic planner is wired in.
The controller takes manually configured candidate jobs, atomically reserves one roboport stock
snapshot, drives fixed productivity/quality/recycler workers, and waits for durable completion latches.

The physical worker pool is:

```text
p0, p1  productivity assemblers
q0, q1  quality assemblers
r0      quality recycler
```

The module roles are fixed. Do not swap modules dynamically.

## Generate the controller

From the repository root:

```bash
uv sync --extra dev
uv run python -m examples.autonomous_mall.manual_controller \
  > autonomous-mall-manual-blueprint.txt
```

The blueprint string is written to stdout. The terminal wiring map is written to stderr and gives every
compiled scalar signal plus the required red/green wire for every input/output marker. Paste the
blueprint into Factorio and use that map when configuring machine control signals.

For a cheap source-level check first:

```bash
uv run pytest tests/examples/autonomous_mall/test_manual_controller.py
```

The full physical compile check is opt-in:

```bash
uv run pytest -m acceptance tests/examples/autonomous_mall/test_manual_controller.py
```

## Shared stock input

Place one roboport in an otherwise isolated logistic network and enable **Read logistic network
contents**. Wire it directly to the compiled `INPUT stock` marker using the color printed by the script.
Do not place job-control signals on this wire.

For the first tests put only normal iron plates, gears, modules, and the worker chests in this logistic
network. Keeping the network isolated makes the stock snapshot easy to inspect.

## One assembler worker

For each of `p0`, `p1`, `q0`, and `q1` place:

```text
requester chest -> inserter -> assembling machine 3 -> inserter -> passive/active provider chest
```

Configure the requester chest to **Set requests** from the circuit network and enable **Trash
unrequested items**. Connect the corresponding controller output, for example
`OUTPUT p0_requester_demand`, to that chest.

Connect `OUTPUT p0_recipe` to the assembler and enable **Set recipe** from the circuit network. Install
productivity modules permanently in `p*` assemblers and quality modules permanently in `q*` assemblers.
Do not use speed beacons in the quality test.

Configure **Read working** on the assembler to the concrete scalar signal printed for `INPUT p0_working`
and connect that machine output directly to the input marker.

Configure **Read recipe finished** as a one-tick pulse, but do not connect the raw pulse directly to
`INPUT p0_finished`; it can be shorter than the controller's logical period. Put the pulse through the
persistent completion latch described below.

## Completion latch

Each worker needs a small external latch. Let:

- `S` be any private virtual signal emitted by the machine's **Read recipe finished** option;
- `L` be the concrete scalar signal printed for `INPUT <worker>_finished`;
- `A` be the concrete scalar signal carried by `OUTPUT <worker>_ack_finished`.

Use two decider combinators on a private wire network:

```text
SET:   if S > 0              -> output L = 1
HOLD:  if L > 0 AND A = 0    -> output L = 1
```

Feed the HOLD output back to its own input. Feed SET's output into the HOLD input. Connect the HOLD
network to `INPUT <worker>_finished`. Connect `OUTPUT <worker>_ack_finished` to the HOLD condition
network.

A machine finish pulse sets `L`; the latch keeps it high until the controller reaches WAIT, consumes the
completion, and raises `A`. The controller refuses to start another batch while any `L` remains high.

Use the same structure for the recycler's finish pulse.

## Recycler worker

For `r0` place:

```text
requester chest -> inserter -> recycler -> inserter/belt -> provider chest
```

Connect `OUTPUT r0_requester_demand` to the requester's circuit-set requests. There is deliberately no
`r0_recipe` output: the recycler is furnace-style and chooses its reverse recipe from the inserted item.
Install quality modules permanently in the recycler.

Wire **Read working** and the latched **Read recipe finished** exactly as for an assembler.

## Manual job inputs

Each worker has a persistent job configuration supplied by constant combinators:

```text
<worker>_job_enable   scalar 1 to make this candidate active
<worker>_job_request  exact ingredients/items to request for one physical attempt
<worker>_job_recipe   one recipe/product signal for assembler workers only
```

Use the exact item quality on both recipe and ingredient signals. The recycler has no `job_recipe`
input; its `job_request` is the item to recycle.

The controller does not continuously consume these commands. It samples one scheduling epoch when
`dispatch` is asserted while `batch_ready=1`.

`dispatch` is edge-armed. After a batch is accepted, return `dispatch` to zero before trying another
batch. Leaving it high cannot accidentally repeat the same jobs.

## Test 1: one-shot worker behavior

Start with `p0` only:

```text
p0_job_enable  = 1
p0_job_recipe  = normal iron gear wheel, count 1
p0_job_request = normal iron plate, count 2
```

Put at least two normal iron plates in the logistic network. Assert `dispatch`.

Expected sequence:

1. `p0_accepted` pulses and `p0_busy` becomes true.
2. `p0_requester_demand` requests exactly two plates and `p0_recipe` selects iron gear wheel.
3. When the assembler reports working, both request and recipe outputs disappear.
4. The already-started craft finishes exactly once.
5. The finish latch rises; `p0_ack_finished` clears it; `p0_busy` returns to zero.
6. No second craft starts even if robots briefly delivered excess material.

Return `dispatch` to zero. After the finish latch is clear and the machine is idle, `batch_ready` and
`dispatch_armed` return high.

## Test 2: atomic reservation

Put exactly **two** normal iron plates in the network. Configure both `p0` and `q0` as one iron-gear
craft, each requesting two normal plates, and enable both jobs.

Assert `dispatch` once.

Expected: `p0` is accepted and `q0` is not. Worker order is deterministic, so `p0` reserves the two
plates before `q0` is considered. The roboport signal may later fall while robots fly; that cannot create
another allocation because the batch is already closed.

Now repeat with **four** plates. Expected: both `p0` and `q0` are accepted in the same batch and run in
parallel.

This is the primary anti-oscillation/multi-assembler acceptance test.

## Test 3: quality production

Leave `q0` configured for one normal iron-gear craft and install four quality modules in its assembler.
Using higher-quality quality modules makes the experiment faster to observe.

Run repeated single-worker batches by toggling `dispatch` low/high only after each previous transaction
has fully cleared. Each attempt must consume exactly one craft's requested normal plates. Inspect the
provider chest or the roboport signal after each attempt.

Expected: most gears remain normal and occasional gears have higher quality. Regardless of outcome, each
dispatch causes only one craft. The stochastic result is observed in real inventory rather than predicted
by the controller.

## Test 4: recycler

Place several normal or higher-quality iron gears in the logistic network. Configure:

```text
r0_job_enable  = 1
r0_job_request = one iron gear wheel at the exact quality you want to recycle
```

Disable the assembler jobs and dispatch one batch at a time.

Expected: the recycler consumes one requested gear per transaction and may return zero, one, or more
plates according to recycler randomness. A zero-output recycle still completes correctly because the
transaction closes from the latched recipe-finished signal, not by waiting for an output item.

With quality modules in the recycler, returned plates may be upgraded in quality.

## Test 5: all five workers

After the first four tests work, give every worker a candidate job and enough stock for all five. Assert
one dispatch.

Expected:

- every affordable candidate is accepted in deterministic `p0, p1, q0, q1, r0` order;
- all accepted workers may execute concurrently;
- no later candidate can spend stock already reserved by an earlier candidate in the same batch;
- no second scheduling epoch occurs until every worker is idle, every finish latch is clear, and
  `dispatch` has gone low once.

## What this test does and does not validate

This validates the physical transaction contract needed by the autonomous mall: roboport snapshot input,
requester-chest transport, fixed P/Q/R worker roles, atomic same-batch reservations, one-shot recipe
control, durable completion, multi-worker execution, and the absence of stock-feedback oscillation during
robot flight.

The global material-efficiency planner in `planner.py` is still a Python reference oracle. The next
controller milestone is to realize its decision rule (or a proved-equivalent/local approximation) in
circuit logic and feed the jobs in this document automatically. Until then, the job constant combinators
stand in for that planner.
