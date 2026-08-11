# State and Timing Design

This document records the current state/timing direction after the symbolic-frontend refactor and the
first state-timing scheduler milestone.

## 1. Three semantic layers

Keep these concepts separate:

```text
logical stream/sample identity
    which external/state sample an expression denotes

state transition ordering/time
    which state boundary a transition belongs between

physical phase
    when a Factorio realization can actually produce/consume the value
```

The compiler may change physical phase while preserving the first two.

## 2. Symbolic stream frontend

The public language is ordinary Python elaboration over symbolic objects:

```python
c = Circuit("example")
x = c.input("x")
y = (x + 1) * 2
c.output("y", y)
```

`Expr` objects represent logical streams. Their execution tick is opaque to source code.

Raw sources are distinct objects with temporal capabilities:

```text
Input / SignalsInput
    external source
    .sample() available

Expr
    derived scalar stream
    operators available
    physical execution phase opaque
```

## 3. Fresh external observations

Every description is interpreted relative to invocation index `t`; the freshness cursor begins at
`τ=0`.

```python
x0 = x
c.tick(3)
x3 = x.sample()
```

means

```text
x0[t] = X[t]
x3[t] = X[t+3]
```

`c.tick()` changes only the cursor used by later observations. Previously built expressions remain the
same streams.

## 4. State reads name exact logical boundaries

```python
before = memory.value
c.tick(3)
after = memory.value
```

creates reads of `S[t]` and `S[t+3]`. Every read also receives a per-state elaboration-order identity.

A read does not mean "whatever state happens to be physically available here". Its freshness offset
is semantic and exact. The physical scheduler must realize that observation or reject the program.

## 5. One compound transition per current vector register

The trusted physical components each describe one state transition per invocation.

For `AccumulatorReg`, any number of `.add(...)` calls are commutative sources in one transition:

```python
memory.add(a, when=enable_a)
memory.add(b, when=enable_b)
memory.clear(when=clear)
```

which means:

```text
clear != 0   next = {}
clear == 0   next = current + enabled(a) + enabled(b) + ...
```

For `FreezeReg`:

```text
set != 0   next = data
set == 0   next = current
```

This matters for ordering. A read may bracket the compound transition:

```python
old = memory.value
memory.add(data)
memory.clear(when=clear)
c.tick(1)
new = memory.value
```

A read between `.add(...)` and `.clear(...)` is currently rejected because the concrete accumulator has
no intermediate "after add but before clear" state.

Individual update IR records are still retained so a future finer-grained event model can relax this.

## 6. Elastic semantic commit offset

For an unanchored transition, the compiler chooses an integer `k >= 0`:

```text
update produced by invocation t
    commits between S[t+k] and S[t+k+1]
```

Strict elaboration order turns reads into bounds on `k`:

```text
read before update at offset r     r <= k
read after update at offset r      k < r
```

The compiler currently chooses the earliest legal `k`.

Example:

```python
old = reg.value        # offset 0
reg.set(data, when=e)
c.tick(3)
new = reg.value        # offset 3
```

permits `k ∈ {0,1,2}` and therefore chooses `k=0`.

A read after the update at the same freshness offset gives `k < 0`, so compilation fails. This is the
intended strict-v1 answer to "how do I access the previous state while a complex update is in flight?":
obtain that old observation before issuing the transition, and request the new observation at a later
logical boundary.

## 7. Physical state phase

Every register also gets a physical phase `P`:

```text
logical S[t] is observable at physical tick t + P
```

This is independent of semantic commit offset `k`.

The state timing analysis first computes the earliest physical availability of all update operands.
State-to-state whole-vector feeds add difference constraints between register phases. Zero-weight cycles
are valid and model mutually coupled synchronous state; a positive-weight cycle is infeasible because it
would require a value to arrive strictly before itself. For the current Factorio-native accumulator/freeze
prototypes the solver chooses the smallest non-negative phases satisfying all such constraints.

The solved information is available as `CompilationResult.state_timing`.

```text
RegisterTiming
    commit_offset
    state_phase
    transition_input_phase
    earliest_transition_input_phase
    read physical phases
```

The physical lowerer consumes this plan rather than independently guessing register alignment.

## 8. Reference state simulation

The semantic simulator now supports the current whole-vector state primitives. It uses the same
semantic commit offsets while evaluating the transition equations directly.

This lets tests compare:

```text
logical state transducer
        vs
physical tick-accurate Factorio circuit
```

at every declared output phase.

That comparison is now used for both `AccumulatorReg` and `FreezeReg`, including a delayed/complex
update case.

## 9. Representative temporal circuits

Timing tests should be reusable circuits rather than isolated one-off expressions. The shared cases
live in `tests/support/circuits.py`.

### Delayed accumulator window

`delayed_accumulator_window(offset=3)` has:

- an old read at offset 3;
- a multi-stage clear predicate;
- one accumulator compound transition;
- a new read at offset 4.

The reads force `commit_offset == 3`; the operand DAG independently determines the physical state phase.
This case exercises the central separation between semantic state time and physical latency.

### N-tick pulse stretcher

`n_tick_pulse_generator(n)` is deliberately stateless. It constructs a lookahead window from fresh
samples. A one-tick trigger produces exactly `n` consecutive high output ticks after inferred latency;
nearby or longer trigger pulses retrigger and merge these windows.

This is a compact regression case for:

- freshness provenance;
- phase alignment;
- phase growth with `n`;
- semantic/physical stream correspondence.

It complements the state tests rather than replacing them.

### Switchable Fibonacci

`switchable_fibonacci()` is the first coupled-state stress test. Two zero-initial registers use the affine
recurrence

```text
A' = B
B' = B + A + 1
```

when `on != 0`, and both hold when `on == 0`. After the transition, `B' - A'` yields
`1, 1, 2, 3, 5, ...`. The circuit deliberately uses `FreezeReg` for assignment and two conditional
`AccumulatorReg.add(...)` sources for the additive update.

The test exercises:

- simultaneous state-to-state reads and writes;
- a zero-latency timing cycle between two registers;
- hold/resume behavior;
- a strict post-transition observation at `tick(1)`;
- semantic/physical equivalence with and without optimization.

It also established a physical lowering invariant: a whole-vector value denotes a concrete red-wire
network. Extracting one signal lane into scalar logic is materialized through an isolating combinator.
This prevents scalar consumers from merging independent register feedback networks.

## 10. Explicit semantic write timing is next

Some transitions genuinely belong to a programmer-selected logical boundary, such as timer updates.
The intended future shape remains something like:

```python
timer.add(1, at=c.now)
```

`at=` will constrain semantic commit offset rather than physical Factorio phase. With the current
solver in place, this should become an additional equality/bound constraint instead of a separate
scheduling mechanism.

The exact API remains intentionally unexposed until the timer-like representative case is designed.

## 11. Future update-event ordering

Strict elaboration order remains a v1 simplification. The IR preserves individual update identities so
future syntax may express relations such as:

```python
u = reg.set(expr)
old = reg.read(before=u)
new = reg.read(after=u)
```

without replacing the underlying state representation.

The likely implementation path is to replace the current total-order-derived commit window by a
partial-order constraint graph.

## 12. Current limitations

1. explicit semantic write-time anchoring (`at=`) is not yet exposed;
2. future-sampled external sources feeding feedback state need a deliberate startup/warm-up convention;
3. state-derived scalar update controls are not yet supported by the timing solver;
4. current vector registers support one compound transition per invocation;
5. accumulator commutativity is represented within a transition, while more aggressive physical merging
   and scheduling optimizations remain future work;
6. counters, timers, queues, stacks, and general memories remain later state types;
7. interleaving and processor-like temporal resource sharing remain postponed.
