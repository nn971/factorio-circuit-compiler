# Manual in-game autonomous mall test

This test exercises the physical transaction layer before the final circuit-side economic planner is
wired in. The controller takes manually configured candidate jobs, atomically reserves one roboport
stock snapshot, drives fixed productivity/quality/recycler workers, and waits for durable completion
latches.

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
compiled scalar signal plus the required red/green wire for every input/output marker.

Cheap source-level check:

```bash
uv run pytest tests/examples/autonomous_mall/test_manual_controller.py
```

Opt-in full physical compile:

```bash
uv run pytest -m acceptance tests/examples/autonomous_mall/test_manual_controller.py
```

## Shared stock input

Place one roboport in an otherwise isolated logistic network and enable **Read logistic network
contents**. Wire it directly to `INPUT stock` using the color printed by the generator. Keep job-control
signals off this wire.

For the first tests use normal iron plates and iron gears only. The one-ingredient gear recipe makes the
one-shot feed protocol easy to verify before generalizing the external worker device.

## Assembler worker

For each of `p0`, `p1`, `q0`, and `q1` place:

```text
requester chest -> stack-size-1 inserter -> assembling machine 3 -> inserter -> provider chest
```

Configure the requester chest to **Set requests** from the circuit network and enable **Trash
unrequested items**. Connect `OUTPUT <worker>_requester_demand` to it.

Connect `OUTPUT <worker>_recipe` to the assembler and enable **Set recipe**. The recipe signal is
intentionally persistent between jobs. This preserves partial productivity-bar progress when a P worker
runs the same recipe again.

Install productivity modules permanently in `p*` machines and quality modules permanently in `q*`
machines. Do not use speed beacons for the first quality test.

### Input inserter gate

The controller does **not** stop a job by removing the recipe. Factorio can begin another craft before a
post-finish disable reacts, and changing/removing a productivity recipe can discard partial productivity
progress. Instead, starve the machine after the first craft has begun.

Set the input inserter's stack size to **1** and enable it only when both conditions hold:

```text
controller OUTPUT <worker>_input_enable > 0
machine Read-working signal = 0
```

Use a small decider if you need to combine those two signals before the inserter. The important point is
that the machine's own Read-working signal reaches the inserter locally, without waiting for the compiled
controller to notice the state change.

For the iron-gear test, the inserter moves plate 1, then plate 2; the assembler starts immediately after
plate 2, Read-working becomes nonzero, and the inserter is blocked before it can feed a third plate.

### Working signal

Configure **Read working** on the assembler to the concrete scalar signal printed for
`INPUT <worker>_working` and connect it directly to that input marker.

### Durable completion latch

Do not wire the one-tick **Read recipe finished** pulse directly to `INPUT <worker>_finished`; the
controller's logical period may be longer than one game tick.

Let:

- `S` be any private virtual signal emitted by the machine's Read-recipe-finished option;
- `L` be the scalar signal printed for `INPUT <worker>_finished`;
- `A` be the scalar signal carried by `OUTPUT <worker>_ack_finished`.

Use two decider combinators on a private network:

```text
SET:   if S > 0              -> output L = 1
HOLD:  if L > 0 AND A = 0    -> output L = 1
```

Feed HOLD back to itself and feed SET into HOLD. Connect the resulting `L` to
`INPUT <worker>_finished`; connect `OUTPUT <worker>_ack_finished` to the HOLD condition network.

The finish pulse sets `L`, HOLD preserves it, and the controller acknowledgement clears it. No new batch
can begin while any worker's completion latch is still high.

## Recycler worker

For `r0` place:

```text
requester chest -> stack-size-1 inserter -> recycler -> inserter/belt -> provider chest
```

Connect `OUTPUT r0_requester_demand` to the requester's circuit-set requests. There is deliberately no
`r0_recipe`: a recycler chooses its reverse recipe automatically from the inserted item.

Gate the recycler input inserter using `OUTPUT r0_input_enable` and local Read-working exactly as for an
assembler. Wire Read-working and the durable Read-recipe-finished latch in the same way. Install quality
modules permanently in the recycler.

## Manual job inputs

Supply each candidate job with constant combinators:

```text
<worker>_job_enable   scalar 1 to make this candidate active
<worker>_job_request  exact ingredients/item for one physical attempt
<worker>_job_recipe   one recipe/product signal for assembler workers only
```

Use exact item quality on recipe and ingredient signals. The recycler has no `job_recipe`; its request is
the item to recycle.

The controller samples one scheduling epoch when `dispatch` is asserted while `batch_ready=1`.
`dispatch` is edge-armed: after a batch fires, return it to zero before requesting another batch.
Leaving it high cannot repeat the same jobs.

## Test 1: one-shot assembler

Enable only `p0`:

```text
p0_job_enable  = 1
p0_job_recipe  = normal iron gear wheel, count 1
p0_job_request = normal iron plate, count 2
```

Put at least two normal plates in the network and assert `dispatch`.

Expected sequence:

1. `p0_accepted` pulses and `p0_busy` rises.
2. `p0_requester_demand` requests two plates; `p0_recipe` selects iron gear wheel.
3. The stack-size-1 feeder inserts exactly two plates.
4. Read-working rises; the local inserter gate stops feeding immediately.
5. The requester demand disappears after the controller observes working, but the recipe remains selected.
6. Exactly one gear craft finishes.
7. The completion latch rises, `p0_ack_finished` clears it, and `p0_busy` returns to zero.

Return `dispatch` to zero. Once the worker/latch are clear, `batch_ready` and `dispatch_armed` return high.

## Test 2: atomic reservation and parallel workers

Put exactly **two** normal plates in the network. Configure both `p0` and `q0` as one iron-gear craft,
each requesting two plates, and enable both.

Assert `dispatch` once.

Expected: `p0` is accepted and `q0` is not. Allocation order is deterministic, so `p0` subtracts its two
plates from the batch snapshot before `q0` is evaluated.

Repeat with **four** plates. Expected: both workers are accepted in the same batch and run concurrently.
The later roboport decrease while robots fly cannot trigger duplicate work because the scheduling epoch is
already closed.

## Test 3: productivity progress survives transactions

Keep `p0` on iron gears and install four normal productivity module 3s. Repeatedly run one-craft batches
without changing the `p0_job_recipe` signal.

Expected: the productivity bar survives across transactions. With +40% total productivity, it should
accumulate across crafts and periodically produce an extra gear instead of resetting to zero after every
job. This is the acceptance test for persistent recipe selection.

If you deliberately change `p0_job_recipe`, losing the old recipe's partial productivity progress is
expected and should later become part of the planner's recipe-switching cost.

## Test 4: quality production

Configure `q0` for one normal iron-gear craft and install four quality modules. Higher-quality modules make
upgrades easier to observe.

Run repeated single-craft batches, toggling `dispatch` low/high only after the previous transaction has
fully cleared.

Expected: most gears stay normal and occasional outputs have higher quality. Every dispatch still causes
one physical craft; quality randomness changes the observed inventory, not the number of requested crafts.

## Test 5: recycler, including zero output

Put gears in the network and configure:

```text
r0_job_enable  = 1
r0_job_request = one iron gear wheel at the exact quality to recycle
```

Disable assembler jobs and dispatch one batch at a time.

Expected: one gear is consumed per transaction. A recycle may return zero, one, or more plates. Even a
zero-output recycle completes because completion is driven by the latched recipe-finished signal rather
than by observing an output item. With quality modules, returned plates can be upgraded.

## Test 6: all five workers

Give every worker an affordable candidate job and enough stock. Assert one dispatch.

Expected:

- candidates are considered in `p0, p1, q0, q1, r0` order;
- all affordable workers may execute concurrently;
- no later candidate can spend stock already reserved earlier in the same batch;
- no second scheduling epoch occurs until every worker is idle, every finish latch is clear, and
  `dispatch` has gone low once.

## Current boundary

This validates the physical transaction contract needed by the mall: roboport snapshot input,
requester-chest transport, fixed P/Q/R roles, same-batch reservations, ingredient-starved one-shot
execution, productivity-progress preservation, durable completion, and multi-worker operation without
robot-flight feedback oscillation.

The global material-efficiency planner in `planner.py` is still the Python reference oracle. The next
controller milestone is to realize its decision rule (or a proved approximation) in circuit logic and feed
these job ports automatically. The manual constants stand in for that planner in this acceptance test.
