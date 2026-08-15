# Stateful stream design

## Public model

State is a logical stream, not a physical combinator tick.

```python
old = reg.sample()  # S[k]
reg.set(next_value, when=enable)
circuit.step()
new = reg.sample()  # S[k+1]
```

For `AccumulatorReg`, `.add(...)` and `.clear(...)` together describe one compound logical
transition. For `FreezeReg`, `.set(data, when=...)` describes the transition. The source never names
the physical tick at which the write occurs.

`register.value` is a deprecated compatibility alias for `register.sample()` retained for old callers.
New code uses `.sample()` consistently for inputs and registers.

`circuit.step(n)` moves the logical observation cursor. `circuit.tick()` is reserved for future
explicit physical scheduling and currently has no source-language semantics.

## Domain schedule

Every connected state component belongs to a logical clock domain with inferred physical period
`P`. Register `R` has a physical phase `phi_R` and represents

```text
R[k] at physical tick phi_R + k*P.
```

A transition producing `R[k+1]` may use several physical combinator stages. The analyzer chooses the
smallest `P` for which all recurrence constraints are feasible.

Ordinary state dependencies union registers into the same domain, even when the dependency is only
one-way. Independent state components may have different periods. Cross-period state communication
will require an explicit future rate-crossing primitive rather than implicit same-index arithmetic.

## Why P is necessary

A state-controlled recurrence such as

```python
old = slot.sample()
occupied = old.any()
slot.set(data, when=occupied)
```

contains real physical latency between `slot[k]` and the decision for `slot[k+1]`. Requiring one
logical transition every game tick incorrectly turns this into an impossible positive-latency
cycle. Instead the scheduler infers `P=3` for the current realization:

1. `old.any()` comparison;
2. state-control normalization;
3. write gate whose output is the next state.

Feed-forward pipelines do not impose such a period because they can overlap consecutive samples.
Only recurrences constrain the initiation interval.

## Timing representation

The analyzer keeps logical displacement and physical latency separate. A requirement records:

```text
(source register or external source, logical offset, physical latency)
```

For target commit offset `c`, source logical offset `r`, physical latency `L`, and shared domain
period `P`, a state dependency becomes

```text
phi_target >= phi_source + (r - c - 1) * P + L + 1.
```

For a fixed candidate `P`, these are ordinary difference constraints. The compiler tries periods
from one upward and selects the first feasible value. A zero-logical-distance cycle with positive
physical latency remains impossible for every `P` and is rejected.

## Physical state cells

The logical register abstractions remain whole-vector cells.

### FreezeReg

At a logical boundary:

- `set != 0`: pass the selected new vector into memory;
- `set == 0`: feed memory back to itself.

For `P>1`, a modulo-domain clock additionally forces the feedback/hold path on intermediate game
ticks. Thus changing external inputs during the window does not create extra logical writes.

### AccumulatorReg

At a logical boundary:

- enabled additions enter the memory network;
- `clear != 0` suppresses retained memory and additions;
- otherwise memory is retained and enabled additions are accumulated.

For `P>1`, additions are enabled only on the register's scheduled domain residue. Clear is ignored
between boundaries while memory keeps circulating.

The P=1 and P>1 implementations use the same condition structure so analyzer latency and physical
lowering stay aligned.

## Elaboration ordering

Register accesses retain strict order within elaboration. Reads before all transition calls observe
the old state; reads after all transition calls must occur at a later logical step. A read between
members of one compound accumulator transition is rejected.

This ordering determines legal logical commit offsets, while physical phase and period remain
compiler choices.

## Current boundaries

- Registers start from the existing zero-vector initial state model.
- Different state domains can be inferred, but nonzero-step external samples used across
  heterogeneous periods are not yet lowered context-sensitively.
- Explicit clock-domain crossing between state streams is not yet implemented.
- `Circuit.tick()` is intentionally reserved rather than pretending to mean logical time.
- Higher state structures (queues, stacks, heaps) should first be constructed from these general
  primitives; they are not compiler primitives by default.

## Semantic Event capture boundary

`FreezeReg.capture_on(trigger, value=None, required_min_separation=...)` is a semantic/reference-only
transition. Scalar Event triggers require an explicit vector value; vector Event triggers may capture
their payload. Captures use only zero-offset Level/state values, run on deterministic schedules, and
commit atomically for same-timestamp Events. Event modules are rejected by the periodic compiler and
ordinary Level simulator; physical pulse capture, buffering, and handshake behavior are future work.
Event captures and `event_state_operations` are outside `StateTimingPlan`; an Event-bearing module
is elaborated into a semantic `CircuitModule`, then a Level/physical route raises
`EventCompilationError` before state-timing analysis or semantic-to-physical lowering. No physical IR,
synthesis, or blueprint output follows.

`Circuit.sample_on(...)` is not a state transition. It records a raw Level value when its declared
Event target activates, and its observation is carried by the semantic Event reaction. A
`SampleOnReference` is a non-expression reference and cannot be passed to `capture_on` or emitted as
an output. Reference-only HOLD/ZERO/VALID materialization is performed after simulation and is not
hardware; it does not introduce a register, pulse, bridge, valid wire, or physical output policy.
