# Autonomous market prototype

## Goal

Build a first autonomous market with exactly one recipe-reader assembler and one worker assembler.
The prototype discovers and satisfies intermediate requirements from live stock/recipe feedback
without storing product quantity per recipe or a full static recipe database.

The market controller is ordinary circuit source. Do **not** add an FSM, stack, or queue primitive to
the compiler. Control state and recursive task storage are composed from the existing register and
runtime-open vector operations.

## Status

The prototype has been compiled to a routed Factorio blueprint and tested in game with a real recipe
reader and worker. Recursive prerequisite discovery and production work. The reader required one
explicit logical settling interval before its ingredient vector could be consumed reliably; this is
now part of the controller protocol.

The current scheduler may overproduce when the worker finishes the last required craft because the
new product can sit in the assembler output or logistics system before the external `stock` signal
reflects it. The controller can therefore observe stale stock and request another craft. This is a
market scheduling/observation limitation, not a compiler bug. Future work should either count worker
output/in-flight inventory in effective stock or introduce a more explicit completion/availability
policy.

This prototype is intentionally paused at this point.

The Phase 3/4 Event work does not migrate this controller. It adds only semantic/reference schedules,
captures, SampleOn observations, and reference materialization; physical completion pulses, buffering,
handshakes, and Event-to-periodic integration remain unresolved. There is no deployed autonomous-
market Event migration; the market continues to use its existing Level-based protocol.

## Task representation

A task is an arbitrary runtime-open threshold vector `T`:

```text
stock[item] >= T[item]
```

for every active lane in `T`.

Its current deficit is:

```python
missing = (T - stock).positive()
```

The task is satisfied iff `missing.any()` is false. Otherwise `missing.max()` chooses one currently
missing lane to investigate/craft. The selected lane count is not treated as recipe/product
metadata; the whole task vector remains the authoritative threshold.

## Why a stack, not a FIFO

Recipe resolution is recursive and naturally depth-first. If a parent task discovers a missing
ingredient threshold vector, push that prerequisite above the parent. When the prerequisite is
satisfied, pop it and the parent immediately becomes current again.

For a four-slot compact stack with slot 0 as top:

```text
push X: [A, B, C, _] -> [X, A, B, C]
pop:    [A, B, C, _] -> [B, C, _, _]
```

The compact-prefix invariant means:

```text
empty <=> slot0 is empty
full  <=> slot3 is nonempty
```

so no length accumulator is needed. `examples/vector_stack.py` realizes this directly with four
`FreezeReg`s. The older FIFO remains useful as a timing regression but is not the market worklist.

## Controller state

`examples/autonomous_market_controller.py` uses only six primitive registers:

- `slot0..slot3`: depth-four task stack;
- `mode`: one-hot controller mode in a `FreezeReg`;
- `selected_item`: one-lane selected item held across reader/worker interaction.

There is no compiler FSM object. Zero `mode` means CHECK/IDLE. Four virtual lanes represent
QUERY_WAIT, QUERY_EVAL, START_WORKER, and WAIT_WORKER. Because the mode and stack transitions depend
on one another, timing analysis naturally places them in the same inferred logical clock domain and
synthesizes the required physical scheduling clock.

## Control algorithm

When the stack is empty, a persistent unsatisfied `root_target` is pushed as the bottom task.
Otherwise process the top task:

```text
CHECK top
  |
  +-- satisfied ----------------------------------------------> POP -> CHECK
  |
  `-- missing -> choose missing.max()
                    |
                    v
               QUERY_WAIT
            assert reader_item
                    |
                    v
               QUERY_EVAL
                    |
                    +-- prerequisites missing --> PUSH prerequisite -> CHECK
                    |
                    `-- prerequisites ready ----> START_WORKER
                                                    |
                                          Read working becomes 1
                                                    |
                                          withdraw worker recipe
                                                    |
                                               WAIT_WORKER
                                                    |
                                          Read working becomes 0
                                                    |
                                                    v
                                               CHECK same top
```

After an observed craft the task is deliberately **not** decremented by a predicted product
quantity. The controller re-reads stock and evaluates the same threshold again. This is the central
feedback mechanism of the prototype.

## Reader interface

There is no `reader_ready` handshake. `reader_item` itself is the recipe request. The controller
keeps it asserted across two logical phases:

1. QUERY_WAIT: assert the selected recipe and deliberately ignore `reader_ingredients`;
2. QUERY_EVAL: keep the same recipe asserted and then evaluate the ingredient vector.

The first in-game version tried to assert the recipe and evaluate ingredients in the same logical
query phase. Although the reader assembler switched to the correct recipe, the controller could
consume the previous/empty ingredient vector before the external assembler response had propagated.
Adding one complete logical settling interval fixed the behavior in game. This is concrete evidence
that external device latency is not represented by the compiler's internal combinator timing model.

The reader's Set-recipe input and Read-ingredients output should remain on separated circuit networks
so its output is not fed back into recipe selection.

## Worker interface via Read working

There is no synthetic `worker_done` signal. `worker_item` itself is the recipe request.

The controller uses the worker assembler's `Read working` level in two phases:

1. START_WORKER: assert `worker_item` until `worker_working != 0` is observed;
2. WAIT_WORKER: withdraw `worker_item` and wait until `worker_working == 0` again.

Withdrawing the circuit-set recipe after observing `working=1` lets the already-started craft finish
while preventing another craft from being intentionally requested. The falling working level sends
the controller back to CHECK.

Two scheduling caveats remain:

- `Read working` is a level. If an entire craft starts and finishes between logical controller
  observations, a slow domain could miss the working interval. This is related to the triggered
  domain/input-capture problem in `docs/timing-open-problems.md`.
- A completed product may not yet be visible in external `stock` when working falls to zero. If the
  product is still in the assembler output slot or in logistics transit, CHECK may see a stale
  deficit and start an unnecessary extra craft. A stronger scheduler should define effective stock
  to include locally owned/in-flight material or otherwise wait for availability feedback.

## Current physical I/O

Inputs:

- scalar: `root_enabled`, `worker_working`;
- vector: `stock`, `root_target`, `reader_ingredients`.

Outputs:

- `reader_item`: selected recipe throughout QUERY_WAIT and QUERY_EVAL;
- `worker_item`: selected recipe until worker working is observed;
- `mode`, `top_target`, `blocked_on_full_stack`: prototype probes.

The recipe vectors themselves are the requests; separate `reader_request`/`worker_request` booleans
are unnecessary.

## Deferred market-level work

The working prototype intentionally leaves several problems for later:

- count assembler output and material in logistics transit as effective stock to avoid feedback
  overproduction;
- handle uncraftable/raw missing items instead of assuming sufficient base stock;
- detect recipe dependency cycles;
- handle stack overflow more gracefully;
- generalize from one worker to multiple workers without oscillation under robot transport delay;
- decide which recipe metadata must be stored in ROM versus inferred dynamically, including product
  quantity, machine compatibility, productivity eligibility, and fluid inputs/outputs.
