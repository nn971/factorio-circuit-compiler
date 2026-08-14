# Autonomous market prototype

## Goal

Build a first autonomous market with exactly one recipe-reader assembler and one worker assembler.
The prototype should discover and satisfy intermediate requirements from live stock/recipe feedback
without storing product quantity per recipe or a full static recipe database.

The market controller is ordinary circuit source.  Do **not** add an FSM, stack, or queue primitive to
the compiler.  Control state and recursive task storage are composed from the existing register and
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

The task is satisfied iff `missing.any()` is false.  Otherwise `missing.max()` chooses one currently
missing lane to investigate/craft.  The selected lane count is not treated as recipe/product
metadata; the whole task vector remains the authoritative threshold.

## Why a stack, not a FIFO

Recipe resolution is recursive and naturally depth-first.  If a parent task discovers a missing
ingredient threshold vector, push that prerequisite above the parent.  When the prerequisite is
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

so no length accumulator is needed.  `examples/vector_stack.py` realizes this directly with four
`FreezeReg`s.  The older FIFO remains useful as a timing regression but is not the market worklist.

## Controller state

`examples/autonomous_market_controller.py` uses only six primitive registers:

- `slot0..slot3`: depth-four task stack;
- `mode`: one-hot controller mode in a `FreezeReg`;
- `selected_item`: one-lane selected item held across reader/worker interaction.

There is no compiler FSM object.  Zero `mode` means CHECK/IDLE.  Two virtual lanes represent QUERY
and CRAFT.  Because the mode and stack transitions depend on one another, timing analysis naturally
places them in the same inferred logical clock domain and synthesizes the required physical clock.

## Control algorithm

When the stack is empty, a persistent unsatisfied `root_target` is pushed as the bottom task.
Otherwise process the top task:

```text
CHECK top
  |
  +-- satisfied ------------------------------> POP -> CHECK
  |
  `-- missing -> choose missing.max()
                    |
                    v
                  QUERY reader
                    |
                    +-- prerequisites missing --> PUSH prerequisite vector -> CHECK
                    |
                    `-- prerequisites ready ----> CRAFT selected item once
                                                    |
                                                    v
                                                  CHECK same top again
```

After a craft the task is deliberately **not** decremented by a predicted product quantity.  The
controller simply re-reads stock and evaluates the same threshold again.  This is the central
feedback mechanism of the prototype.

## Reader/worker protocol for the current timing model

Arbitrary one-game-tick pulses can be missed by a `P>1` domain.  Until triggered-domain/event capture
semantics are implemented (see `docs/timing-open-problems.md`), the prototype environment must use
held level handshakes:

- `root_target` is persistent while `root_enabled` is high;
- while `reader_request` is high, the reader holds `reader_ingredients` and `reader_ready` stable;
- while `worker_request` is high, the worker holds `worker_done` high after completion until the
  request is withdrawn.

This is a prototype protocol, not the final language-level solution for asynchronous pulse inputs.

## Next milestone: fake environment

Build a tiny controlled environment around the controller before wiring real assemblers:

1. provide a persistent root threshold such as `electronic-circuit >= 5`;
2. make the fake reader return one-craft ingredient threshold vectors;
3. make the fake worker update stock after a held craft request;
4. deliberately give at least one recipe product quantity greater than one (for example cable
   producing two units per craft);
5. verify that the controller recursively pushes prerequisites, resumes parents, and converges using
   stock feedback without knowing the product quantity.

Keep raw/base resources pre-stocked for this first environment; detecting uncraftable/raw leaves and
recipe dependency cycles are later market-level problems.
