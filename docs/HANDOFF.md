# Fresh-Chat Handoff

## Goal

Compile a symbolic Python circuit EDSL to optimized Factorio 2.x combinator blueprints, with
Factorio-specific optimization primarily reducing combinator count.

## Frontend

The canonical frontend is ordinary Python elaboration over symbolic stream objects:

```python
c = Circuit("example")
x = c.input("x")
y = (x + 1) * 2
c.output("y", y)
```

Runtime selection uses symbolic operations such as `condition.select(a, b)`; Python `if`/loops remain
elaboration-time Python.

## Streams and freshness

Raw external sources are sampleable:

```python
x0 = x
c.tick(3)
x3 = x.sample()
```

means `x0[t]=X[t]`, `x3[t]=X[t+3]`.

Derived `Expr` values are logical streams with opaque physical execution phase and have no `.sample()`.
Whole-vector `SignalsInput` sources are sampleable too.

## State primitives

`AccumulatorReg` and `FreezeReg` remain the trusted whole-vector physical prototypes.

```text
AccumulatorReg:
clear != 0   next = {}
clear == 0   next = current + data

FreezeReg:
set != 0     next = data
set == 0     next = current
```

For the current backend, all update methods on one register describe one **compound transition per
invocation**. `AccumulatorReg.add(...)` and `.clear(...)` are therefore not serial writes.

## State timing milestone completed

State reads carry exact freshness offsets and per-state elaboration-order identities. The compiler now
builds an abstract `StateTimingPlan` before physical lowering.

Each elastic transition gets a compiler-chosen semantic commit offset `k`:

```text
invocation t update commits between S[t+k] and S[t+k+1]
```

Strict reads give bounds:

```text
read before transition at r    r <= k
read after transition at r     k < r
```

A same-freshness read after an update is rejected. To preserve the old value, read it before issuing
the transition; to demand the new value, advance the freshness cursor to the desired logical boundary.

The timing plan separately solves a physical state phase `P`, update-input alignment phase, and every
state-read physical phase. The vector register lowerers consume this plan.

## Reference simulation and reusable tests

The semantic simulator now evaluates both current vector registers and is compared tick-for-tick with
the physical simulator.

Reusable representative circuits live in `tests/support/circuits.py`:

- `delayed_accumulator_window(offset=3)` brackets a complex elastic update by old/new state reads;
- `n_tick_pulse_generator(n)` is a retriggerable `n`-tick pulse stretcher stressing fresh sampling
  and alignment independently of state;
- `switchable_fibonacci()` couples `FreezeReg` and `AccumulatorReg` in a zero-latency state cycle,
  holds while disabled, resumes when enabled, and reads the post-transition boundary at `tick(1)`.

The Fibonacci case also forced scalar extraction from whole-vector networks to become explicit: `.signal(...)`
is lowered through an isolating combinator before ordinary scalar arithmetic, so separate feedback networks
are never electrically merged by a consumer.

Current test status: **52 passed** in the provided environment.

## Important distinction

```text
sample/freshness
    which external/state logical sample is meant

semantic state commit
    between which state boundaries an update belongs

physical phase
    when combinators realize the stream value/transition
```

These remain separate compiler concerns.

## Current limitations

- explicit semantic write anchoring (`at=`) is not implemented yet;
- future-sampled external sources feeding feedback state still need a startup/warm-up convention;
- reads cannot split one current compound register transition;
- state-derived scalar update controls are not yet supported by the timing solver;
- update-event partial ordering and more aggressive commutative accumulator optimization remain future work.

## Recommended next step

Design and implement **semantic write-time anchoring** using the timing solver already in place. The
switchable Fibonacci regression now gives confidence in coupled one-step-per-tick state; use a real
timer/pulse-style anchored state circuit as the representative specification before finalizing syntax.
The likely surface form remains `at=c.now`, but the test case should drive whether this is the right API.
After anchored writes work end-to-end, revisit explicit update handles / partial ordering.
