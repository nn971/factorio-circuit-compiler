# Periodic state-cell technology mapping

This note records the first stateful extension of the temporal technology mapper. Ordinary
`FreezeRegister` and the one-add/one-clear `AccumulatorRegister` topology used by Snake now have
candidate-owned timing contracts. Shared delay buses, mapped physical state lowering, and production
compiler integration remain later steps.

## Boundary

The canonical recurrence IR does not assign physical phases to state reads or transitions:

```text
MappingStateRead
    register + logical occurrence

MappingStateTransition
    register + kind + logical occurrence + semantic data/control ids
```

A prescribed `MappingProblem.period` is a throughput/occurrence constraint. It is not a physical
register phase. The selected state-cell candidate owns the relation between logical occurrences and
its target ports.

## Ordinary Freeze candidate

The ordinary Freeze candidate mirrors the established Factorio topology:

```text
semantic when
    -> pass decider ---\
                        +-> transparent data gate -> memory network
    -> hold decider ------------------------------> memory cell

semantic data -----------------------------------> transparent data gate
```

The topology contains four implementation entities:

```text
2 control deciders
1 transparent data gate
1 vector memory combinator
```

Let logical state `S[k+1]` become visible at physical phase `r`. The candidate contract is:

```text
semantic data use = r - 1
semantic when use = r - 2
new state read    = r
```

The one-tick difference between data and condition is candidate-owned: pass/hold normalization is one
Factorio combinator stage. No corresponding latency appears in `MappingStateTransition`.

## Ordinary Accumulator add+clear candidate

The first Accumulator candidate deliberately matches the topology exercised by default Snake: exactly
one conditional add and one clear transition on the same logical occurrence. The add condition is
non-constant.

The existing target topology is conceptually:

```text
clear when -> clear-inactive decider ----+----> one-tick delay ----> memory gate
                                         |
add when   -> add-active decider --------*----> add/clear control
                                               |
add data --------------------------------------*----> gated add -> memory network

memory network ----------------------------------------------------> memory cell
```

Relative to the next state read phase `r`, its external semantic port contract is:

```text
add data   = r - 1
add when   = r - 3
clear when = r - 3
new state  = r
```

The six state-specific implementation entities are:

```text
1 clear-condition normalization decider
1 add-condition normalization decider
1 add-active * clear-inactive control combinator
1 vector add gate
1 clear-inactive one-tick delay
1 vector memory combinator
```

The `r-3` control timing is not a semantic property. Both raw conditions first take one tick to
normalize. Add-active and clear-inactive are then combined for another tick before the vector add gate
consumes the result at `r-1`. Clear-inactive is also needed by the memory at `r-1`, so the same
normalized control is delayed one tick along that second internal path.

This first candidate intentionally rejects other accumulator shapes rather than guessing their cost or
port timing. Future candidates can cover constant/unconditional adds, multiple add sources, missing
clear controls, or more aggressive fused state topologies explicitly.

## Stable read windows

Both first ordinary state-cell families export a stable Level read. If the selected cell has base read
phase `b` and logical period `P`, a `MappingStateRead` at offset `k` is freely reusable on:

```text
[b + kP, b + (k+1)P)
```

A use after the last free phase may preserve the old token through ordinary exact transport. This
availability interval is selected-implementation behavior, not part of the neutral `MappingStateRead`.

## First stateful solver

`solve_periodic_state_mapping_problem()` is generic over `StateCellCandidate` port contracts. The
current ordinary candidate set can now solve mixed Freeze/Accumulator recurrence graphs when supplied
with `ordinary_state_candidates(problem)`.

The solver jointly chooses:

```text
ordinary finite computation candidate selection
ordinary computation phases
one selected state-cell candidate per represented register
base read phase in [0, P)
state-transition data/control use phases
stable state-read reuse
fixed source observation/reuse
prefix-shared private exact transport
```

It intentionally does not yet admit:

```text
wire-sum computation candidates
shared delay buses
state-cell physical lowering
```

The objective is:

```text
ordinary computation entity cost
+ selected state-cell entity cost
+ prefix-shared residual exact transport
```

`validate_periodic_state_plan()` independently reconstructs candidate timing, state read windows,
deliveries, exact lifetimes, and both cost components from the selected plan.

## Throughput implications

Candidate port equations now determine minimum feasible cadence jointly with the transition expression
cones. For the ordinary Freeze cell, the raw condition must exist two ticks before the next read. For
the ordinary one-add/one-clear Accumulator, both raw controls must exist three ticks before the next
read, and their own semantic producer operations may impose additional lead time.

A different physical state implementation may therefore support a smaller period, or use fewer
entities at the cost of a larger period, without any change to semantic IR.

## Snake checkpoint

Default one-step Snake contains:

```text
6 Freeze registers      * 4 entities = 24
3 Accumulator registers * 6 entities = 18
                                      ----
ordinary state cells                  42 entities
```

`tests/mapping/test_snake_state_candidates.py` checks this coverage structurally without invoking
CP-SAT.

The first full recurrence timing solve is available as a diagnostic:

```bash
uv run --with 'ortools>=9.14,<10' \
  python -m benchmarks.snake.analyze_mapping \
  --period 60 \
  --solve-full-state
```

That solve uses the full periodic recurrence graph, ordinary computation candidates, and the nine
ordinary state-cell candidates. It does not consult `StateTimingPlan`, and it still uses private exact
transport only. Its purpose is to validate the joint recurrence formulation before buses or mapped
physical state lowering are added.

## Next step

Once the full Snake recurrence solve is stable, the next two changes should be kept separate:

1. compare the selected register phases and transport objective against the established 60-tick Snake
   timing as a diagnostic, without importing old phases as constraints;
2. teach mapped physical lowering to emit selected Freeze/Accumulator state cells from
   `RealizationPlan` and independently verify the resulting Abstract Physical entity count/timing.

Only after those checks should the shared delay-bus resource be reintroduced into the stateful solver.
