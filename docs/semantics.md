# Core Semantics

## 1. Modules are logical stream transducers

A `Circuit` denotes deterministic behavior over Factorio's discrete signal streams, with persistent
state where required. Logical stream semantics and physical combinator phase are separate layers.

## 2. Symbolic scalar expressions

```python
c = Circuit("m")
x = c.input("x")
y = (x + 1) * 2
```

`x` and `y` are Python objects representing streams. Conceptually,

```text
x[t] = X[t]
y[t] = (X[t] + 1) * 2
```

The Python operations build a logical DAG. The compiler decides when physical combinators produce the
corresponding values.

## 3. Raw sources versus derived expressions

A raw `Input` identifies an externally observable stream and can be sampled again. A derived `Expr`
identifies a logical combination of streams and has opaque physical execution timing.

This capability distinction is intentional:

```python
x.sample()       # valid temporal-source operation
(x + 1).sample() # no such method
```

## 4. Freshness cursor

A circuit starts at freshness offset `0`.

```python
c.tick(n)        # τ += n
c.tick_until(n)  # τ = n, with n >= current τ
```

These operations move the cursor used by later fresh observations. They do not mutate expressions
already built.

At cursor `τ`, `x.sample()` denotes `X[t+τ]`.

```python
x0 = x
c.tick(3)
x3 = x.sample()
y = x0 + x3
```

means

```text
x0[t] = X[t]
x3[t] = X[t+3]
y[t]  = X[t] + X[t+3]
```

The physical lowerer may carry the same live input wire with semantic phase `+3` for `x3` while delaying
`x0` so both required samples meet at the consuming combinator.

## 5. Runtime selection

Python control flow executes during elaboration. Runtime circuit control uses `select`:

```python
result = (a > b).select(a, b)
```

A non-comparison condition is interpreted by nonzero truthiness:

```python
result = enable.select(active, idle)
```

which semantically normalizes `enable != 0` before selecting.

## 6. Whole-vector streams

`Circuit.signals(name)` represents the complete sparse Factorio signal map

```text
SignalId -> signed i32
```

at one external port. Whole-vector inputs are also fresh-sampleable sources. General vector arithmetic
is not yet part of the source language; current vector values primarily feed state components.

## 7. State observations and strict v1 ordering

A state `.value` access creates a distinct `VectorRegisterRead` carrying:

- the state object;
- an exact logical freshness offset;
- a monotonically increasing order identity for that state object.

Current update-method calls receive identities from the same sequence.  V1 therefore gives one strict
elaboration order per state object while the backend keeps the concrete transition event explicit.

For the trusted vector registers, all update methods on one register describe **one compound transition
per invocation**.  In particular, `AccumulatorReg.add(...)` and `.clear(...)` are two inputs to one
transition rule rather than two serial memory writes.  A read may occur before the compound transition
or after it.  A read between `.add(...)` and `.clear(...)` is currently rejected because the physical
prototype has no such intermediate state.

## 8. Elastic semantic commit time

An unanchored compound transition receives a compiler-chosen semantic commit offset `k`:

```text
invocation t creates an update
        ↓
transition occurs between S[t+k] and S[t+k+1]
```

Strict reads constrain `k` without exposing physical latency:

- a read before the transition at offset `r` requires `r <= k`;
- a read after the transition at offset `r` requires `k < r`.

Thus:

```python
old = memory.value
memory.set(data, when=enable)
c.tick(1)
new = memory.value
```

allows `k=0`.  By contrast, a post-update read at the same freshness offset is infeasible and produces
a compile-time diagnostic asking the user to advance the logical observation time.  The programmer
chooses when the new state is required; the compiler chooses the elastic commit point inside the
resulting legal interval.

## 9. Physical state phase

Semantic commit time and Factorio execution phase are solved separately.  Every register receives a
physical state phase `P` such that logical boundary `S[t]` is observable at physical tick `t+P`.
Update operands have independently inferred availability phases.  The timing analysis chooses the
smallest non-negative `P` and inserts alignment delays so the trusted physical register prototype can
realize the selected semantic commit offset.

`CompilationResult.state_timing` exposes the solved plan for diagnostics and tests.  Each register plan
contains:

- `commit_offset` — semantic transition offset `k`;
- `state_phase` — physical shift `P` of the state stream;
- `transition_input_phase` — phase at which update inputs meet the physical state gate;
- `earliest_transition_input_phase` — the operand-derived lower bound;
- physical phases for every state read.

The semantic simulator now evaluates `AccumulatorReg` and `FreezeReg` from the same timing plan, so
representative circuits can be checked against the tick-accurate physical simulator rather than merely
checking blueprint structure.

## 10. Current concrete state components

### `AccumulatorReg`

Whole-vector additive memory with optional clear control.

```python
memory = c.accumulator("memory")
memory.add(data)
memory.clear(when=clear)
c.tick(1)
c.output("memory", memory.value)
```

Its current compound transition is equivalent to:

```text
clear != 0   next = {}
clear == 0   next = current + data
```

### `FreezeReg`

Whole-vector pass/freeze memory.

```text
set != 0   next = data
set == 0   next = current
```

```python
memory = c.freeze("memory")
memory.set(data, when=set_signal)
c.tick(1)
c.output("memory", memory.value)
```

## 11. Representative timing tests

`tests/support/circuits.py` contains reusable temporal circuits used across the timing/integration
suite. Important cases are:

- `delayed_accumulator_window(offset=3)`: brackets one elastic transition by old/new observations while
  giving the clear control a deliberately multi-stage expression;
- `n_tick_pulse_generator(n)`: a stateless retriggerable pulse stretcher built from fresh samples;
- `switchable_fibonacci()`: two mutually coupled registers that advance one Fibonacci step per enabled
  tick, hold while disabled, and expose the post-transition value at `tick(1)`.

Together these cases cover freshness, phase growth, strict state boundaries, simultaneous coupled state
transitions, hold/resume behavior, and end-to-end semantic/physical stream equivalence. Whole-vector
`.signal(...)` extraction is physically isolated before scalar arithmetic so consumers never merge
independent feedback networks.

## 12. Current limitations and next semantics

The current timing solver intentionally covers the trusted one-transition-per-invocation vector state
model.  The following remain open:

1. explicit semantic write-time anchoring (`at=` / logical `now`) for timer-like updates;
2. startup/warm-up semantics when future-sampled external sources feed feedback state;
3. state-derived scalar controls for state transitions;
4. a future partial-order/update-event API such as reads constrained before/after named updates;
5. more aggressive physical optimization of commutative accumulator updates;
6. additional state types such as counters, timers, queues, stacks, and memories;
7. interleaving and temporal resource sharing.

