# Periodic state-cell technology mapping

This note records the first stateful extension of the temporal technology mapper. It is deliberately
narrow: ordinary `FreezeRegister` cells are supported; accumulator cells, shared delay buses, mapped
physical state lowering, and production compiler integration remain later steps.

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

The first candidate mirrors the established Factorio topology:

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

The memory output is stable between updates. If the selected cell has base read phase `b` and logical
period `P`, a `MappingStateRead` at offset `k` is freely reusable on:

```text
[b + kP, b + (k+1)P)
```

A use after the last free phase may preserve the old token through ordinary exact transport. This
availability interval belongs to the selected Freeze implementation rather than the neutral state
read record.

## First stateful solver

`solve_periodic_state_mapping_problem()` currently solves:

```text
ordinary finite computation candidate selection
ordinary computation phases
one Freeze state-cell candidate per represented register
base read phase in [0, P)
state-transition data/control use phases
stable state-read reuse
fixed source observation/reuse
prefix-shared private exact transport
```

It intentionally does not yet admit:

```text
AccumulatorRegister cells
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

## Throughput implication

The ordinary Freeze candidate requires the semantic condition two ticks before the next read. A
one-tick logical period is therefore infeasible for this implementation. That result is now produced
by candidate timing constraints in the joint CP-SAT problem rather than by the old state-timing
analyzer.

This distinction is important for future alternatives: a different state-cell implementation may
have a different minimum feasible period and should compete by supplying different port equations and
cost, not by changing semantic IR.

## Next step

The next state candidate should cover the ordinary accumulator topology used by Snake. For the common
one-add/one-clear case, its contract must explicitly account for:

- add-data arrival;
- add-condition normalization;
- clear-condition normalization;
- clear suppression of same-occurrence adds;
- the memory update stage;
- any internal delay needed because the clear-active signal is consumed at more than one phase.

Once Freeze and Accumulator candidates both validate, the full Snake recurrence can enter the
stateful solver under the already validated 60-tick throughput constraint. Only after that solve is
stable should mapped physical state lowering be implemented.
