# Autonomous market prototype

## Goal

Build a first autonomous market with exactly one recipe-reader assembler and one worker assembler.
The prototype should discover and satisfy intermediate requirements from live stock/recipe feedback
without storing product quantity per recipe or a full static recipe database.

The market controller is ordinary circuit source. Do **not** add an FSM, stack, or queue primitive to
the compiler. Control state and recursive task storage are composed from the existing register and
runtime-open vector operations.

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

There is no compiler FSM object. Zero `mode` means CHECK/IDLE. Three virtual lanes represent QUERY,
START_WORKER, and WAIT_WORKER. Because the mode and stack transitions depend on one another, timing
analysis naturally places them in the same inferred logical clock domain and synthesizes the
required physical scheduling clock.

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
                  QUERY reader
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
quantity. The controller simply re-reads stock and evaluates the same threshold again. This is the
central feedback mechanism of the prototype.

## Reader interface

There is no `reader_ready` handshake. `reader_item` itself is the recipe request: it is a one-lane
vector while the controller is in QUERY and empty otherwise. The reader assembler is expected to
react to a circuit-set recipe within at most one physical game tick and expose the corresponding
ingredient vector. QUERY lasts until the next logical controller transition, so this response has
ample physical time to settle before `reader_ingredients` is used.

If real in-game probing contradicts the one-tick assumption, revisit this protocol rather than
silently adding a generic ready signal.

## Worker interface via Read working

There is no synthetic `worker_done` signal. `worker_item` itself is the recipe request.

The controller uses the worker assembler's `Read working` level in two phases:

1. START_WORKER: assert `worker_item` until `worker_working != 0` is observed;
2. WAIT_WORKER: withdraw `worker_item` and wait until `worker_working == 0` again.

Factorio only applies circuit-set recipe changes/removal when the current craft finishes, so
withdrawing the recipe after observing `working=1` does not cancel that craft; it prevents another
craft from starting afterwards. The falling working level then sends the controller back to CHECK.

This still has a timing caveat: `Read working` is a level, not a completion pulse. If an entire craft
starts and finishes between two logical controller observations, the slow domain can miss the
working interval. This is another instance of the triggered-domain/input-capture problem recorded in
`docs/timing-open-problems.md`. For the first prototype, use worker recipes/conditions whose working
interval is observable by the inferred controller period. Do not hide this with compiler FSM logic.

## Current physical I/O

Inputs:

- scalar: `root_enabled`, `worker_working`;
- vector: `stock`, `root_target`, `reader_ingredients`.

Outputs:

- `reader_item`: selected recipe while querying;
- `worker_item`: selected recipe until worker working is observed;
- `mode`, `top_target`, `blocked_on_full_stack`: temporary prototype probes.

The recipe vectors themselves are the requests; separate `reader_request`/`worker_request` booleans
are unnecessary.

## Next milestone: fake environment

Build a tiny controlled environment around the controller before wiring real assemblers:

1. provide a persistent root threshold such as `electronic-circuit >= 5`;
2. make the fake reader update its ingredient vector one physical tick after `reader_item` changes;
3. make the fake worker expose a held working interval for each accepted `worker_item` request and
   update stock when that craft completes;
4. deliberately give at least one recipe product quantity greater than one (for example cable
   producing two units per craft);
5. verify that the controller recursively pushes prerequisites, resumes parents, and converges using
   stock feedback without knowing the product quantity.

Keep raw/base resources pre-stocked for this first environment; detecting uncraftable/raw leaves and
recipe dependency cycles are later market-level problems.
