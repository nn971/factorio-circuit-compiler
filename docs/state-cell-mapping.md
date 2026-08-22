# Periodic state-cell technology mapping

This note records the first stateful extension of the temporal technology mapper. Ordinary
`FreezeRegister` and the one-add/one-clear `AccumulatorRegister` topology used by Snake have
candidate-owned timing contracts, and the already validated scalar delay-bus resource can now
participate in the same solve. Mapped physical state lowering and production compiler integration
remain later steps.

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

A use after the last free phase may preserve the old token through exact transport. The residual
vector lifetime starts at the selected window's final free phase. Because state reads are currently
whole vectors, they remain private in the first scalar bus model; scalar operation/source lifetimes in
the recurrence may use the shared bus.

## Private-only stateful solver

`solve_periodic_state_mapping_problem()` remains the stable baseline. It jointly chooses:

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

`validate_periodic_state_plan()` independently reconstructs candidate timing, state read windows,
deliveries, exact lifetimes, and both cost components from the selected plan.

## Stateful shared-bus solver

`solve_periodic_state_bus_mapping_problem()` adds the same parameterized scalar delay-bus model used by
the stateless joint mapper. It does not run a fixed-placement bus pass after state timing. Instead, the
single CP-SAT model jointly chooses:

```text
ordinary computation phases
state-cell base phases
transition port phases implied by selected cells
exact lifetime lengths
private vs bus transport
bus membership
bus middle span
```

The objective is:

```text
ordinary computation entity cost
+ selected state-cell entity cost
+ private residual exact transport
+ bus middle stages
+ bus ingress/use interfaces
```

The first implementation reuses the established bus model directly. In particular:

- only scalar exact lifetimes may become bus lanes;
- one producer lifetime is either private or assigned wholly to one bus;
- a bus lane requires lifetime length at least three ticks;
- an active bus contains at least two lanes;
- one isolated interface is still charged per transported semantic use;
- state-read vector lifetimes remain private.

For validation, the selected stateful plan is first projected to an all-private shadow plan and checked
by `validate_periodic_state_plan()`. The existing independent delay-bus validator then checks bus lane
spans, interfaces, producer ownership, and cost. This keeps state-cell timing validation and bus
resource validation independent of the CP-SAT extraction path.

`tests/mapping/test_state_bus_mapping.py` contains a controlled two-Freeze recurrence where two long
scalar control lifetimes cost 10 combinators privately and 7 through one shared bus. This confirms
that the same physical resource model is selected inside a recurrence problem rather than only in the
stateless mapper.

## Throughput implications

Candidate port equations determine minimum feasible cadence jointly with the transition expression
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

The first private-only full recurrence solve at `P=60` found:

```text
operation entities = 213
state entities     = 42
private transport  = 355
total              = 610
```

with a proven optimum. The dominant remaining cost is therefore transport rather than state-cell
hardware or semantic computation.

The full recurrence diagnostic now admits the shared bus in the same solve:

```bash
uv run --with 'ortools>=9.14,<10' \
  python -m benchmarks.snake.analyze_mapping \
  --period 60 \
  --solve-full-state \
  --compare-private
```

`--max-delay-buses` and `--bus-capacity` apply to this full-state solve as well as to the output-cone
diagnostic. The command prints the new transport objective, selected bus spans/lane counts, all state
cell phases, and a fresh all-private comparison from the same stateful solver.

## Next step

After the full Snake state+bus solve is validated, the next implementation milestone is deterministic
stateful `RealizationPlan -> AbstractPhysicalCircuit` lowering:

1. emit selected Freeze and Accumulator state cells using the candidate-owned base phases and port
   contracts;
2. lower private exact transport and selected delay buses with the already validated abstract-signal
   isolation rules;
3. independently verify emitted entity counts and phases against the plan;
4. compare the resulting abstract physical Snake implementation against the established accepted path
   before generating a replacement in-game blueprint.

Wire-sum candidates, local fusion/rematerialization, and time-multiplexed functional units should stay
out of this checkpoint so a physical state-lowering failure cannot be confused with another new
technology choice.
