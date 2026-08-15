# Circuit semantics

## Two time coordinates

The source language describes **logical streams**. Factorio realizes them with physical combinator
pipelines. Those are deliberately different coordinates.

For a periodic logical clock domain `D`, logical occurrence `k` is realized with period `P_D`. A
value with physical phase `phi_v` appears at

```text
physical_time(v[k]) = k * P_D + phi_v
```

up to a common origin.

For an Event clock `C`, occurrence times are irregular:

```text
tau_C(0), tau_C(1), tau_C(2), ...
```

A physical phase is still pipeline latency, but it is measured from each semantic occurrence rather
than from a rigid periodic schedule. A long feed-forward pipeline may have large phase while still
accepting a new value every game tick.

## Level, Event, shape, and clocks

External information has two independent classifications:

```text
payload shape:       SCALAR | VECTOR
temporal modality:  LEVEL  | EVENT
```

A Level is persistent information that can be observed at any physical tick. An Event is a sequence
of discrete occurrences where presence is distinct from payload value; scalar zero and an empty
vector are valid Event payloads.

Every clocked `Flow` carries:

```text
payload shape
modality
base clock
logical occurrence offset
```

Clocks describe **when** logical values exist. Modality describes **how** information crosses clocks.
Sampling a Level and preserving an Event history are therefore different operations even if both
sources can change every game tick.

## `.sample()` and flow-local `.step()`

Use `.sample()` to observe Level inputs and state through the compatibility frontend:

```python
x = input.sample()
s = register.sample()
```

`register.value` remains a deprecated compatibility alias for `register.sample()`.

The semantic time operation is flow-local:

```python
later = x.step()
later2 = x.step(2)
```

For a flow on clock `C`,

```text
x.step(n) = x_C[k+n]
```

implemented by increasing the flow's logical occurrence offset. `.step()` never means `n` Factorio
ticks and never silently inserts a register.

The older mutable `Circuit.step()` / `step_until()` cursor remains as compatibility syntax for code
that samples Level/state objects imperatively during elaboration. It is not the fundamental internal
clock model. `Circuit.tick()` remains reserved for future explicit physical-tick constraints.

For Event flows, positive `.step(n)` has a particularly simple stream interpretation: it drops the
first `n` occurrences and then uses the same occurrence-local payload stream. The physical lowerer
realizes this with a shared occurrence counter plus tail-valid gates; it does **not** predict a future
payload or convert the offset into a game-tick delay.

## Stateless expressions

Arithmetic, comparisons, selects, and runtime-open vector operations preserve logical occurrence:

```text
x_C[k] -> combinational logic -> y_C[k]
```

Every physical combinator adds its normal game-tick latency. The compiler delays earlier operands or
valid tokens when logically compatible values reach a physical operation at different phases.
Feed-forward latency alone does not restrict throughput because pipeline stages can overlap.

## Event physical representation

An external Event is lowered as two physical channels:

```text
payload
valid / activation token
```

Payload logic may evaluate speculatively between occurrences. Only `valid=1` denotes semantic
presence. The compiler delays payload and/or valid so consumers see an aligned Event.

This is why zero payload is not absence and why a vector Event can remain one packed signal vector
instead of being split into one clock lane per Factorio signal.

`simulate_events(...)` remains the semantic reference implementation. It executes irregular Event
schedules directly and is used by integration tests as the oracle for physical lowering.

## Explicit clock operations and bridges

Ordinary expression regions are single-clock. Cross-clock behavior is represented explicitly.

### `SampleOn`

```python
sampled = circuit.sample_on(level, event)
```

samples a Level at each target Event occurrence. The Level value is captured at the semantic target
time, then may travel through a longer physical pipeline together with the delayed valid token.

### `GateClock`

```python
gated = circuit.gate_clock(parent, when=predicate)
```

creates a derived subclock. Removing parent activations cannot weaken the parent's known minimum
separation guarantee.

### `EventMerge`

```python
merged = circuit.event_merge(a, b, ...)
```

creates the additive union of same-shaped Event streams. Simultaneous parent payloads are added and
simultaneous activations coalesce to one valid occurrence. A merged clock conservatively has
`guaranteed_min_separation=1` unless stronger structural information is available.

Equivalent merges are interned. Packed vector payloads are merged once and may be shared by multiple
downstream bridges.

### `SumInto`

```python
summed = circuit.sum_into(source, target)
```

preserves additive Event history across a clock crossing. At target occurrence `k` it emits

```text
sum of source payloads in (previous_target, current_target]
```

so a source occurrence simultaneous with the target is **included**. The physical implementation
uses a packed accumulator and snapshot cell.

### `HoldInto`

```python
held = circuit.hold_into(source, target)
```

holds the latest source Event value and exposes it on the target clock. Its boundary is strict-prior:
a source occurrence simultaneous with a target occurrence is **not** visible until the next target.

`HoldInto` elaborates through compiler-owned freeze state plus `SampleOn`, but physical lowering
recognizes the pair as one stateful bridge and gives source and target one common bridge phase.
Sampling at that phase re-observes the live memory net rather than delaying an earlier memory value.

## Bridge phase normalization

Different derived clocks may require different amounts of combinational work before their valid
signals exist. A stateful bridge therefore chooses one physical execution phase `P_B` satisfying all
of its inputs:

```text
source payload -- compute/delay --\
source valid   ------ delay ------+--> bridge cell at P_B
target valid   ------ delay ------/
```

The same latency is measured from each semantic occurrence. Relative semantic ordering is therefore
preserved even when `EventMerge`, `GateClock`, or other derived-clock pipelines have different native
phases.

The bridge cell then defines the simultaneous boundary convention: strict-prior for `HoldInto`,
right-closed for `SumInto`.

## Event-driven state

An Event activation is an atomic logical reaction:

```text
1. observe occurrence-local Event payloads
2. sample Levels and old state
3. evaluate logical expressions
4. determine updates
5. commit new state atomically
```

Semantically this is the same transition model as periodic state, indexed by irregular clock
occurrences instead of a fixed period.

Physical Event state currently supports:

- Event-triggered `FreezeReg.capture_on(...)`;
- unconditional Event `FreezeReg.set(...)`;
- compiler-owned `SumInto` accumulator state;
- one unconditional Event `AccumulatorReg.add(...)` transition per ordinary accumulator.

The last case is lowered directly: the Event payload is valid-gated to additive zero and absorbed by
the destination accumulator feedback cell. No intermediate `SumInto` bridge is created. This is the
implemented bridge/state-fusion case used by the multi-rate event-ledger benchmark.

More general Event accumulator programs—multiple independent Event transitions on one accumulator,
Event clears, or mixed conditional update forms—remain outside the current physical subset and are
rejected rather than approximated.

## Logical causality and physical throughput

Logical causality is checked independently from physical timing. A feedback cycle must contain
strict logical advance. A positive-latency cycle with zero logical displacement is noncausal even if
one tries to slow the clock.

A logically causal Event recurrence may still be too slow for its environment. Each external or
derived clock carries a contract such as

```text
guaranteed_min_separation
```

and state timing derives

```text
required_min_separation
```

Direct realization requires

```text
guaranteed_min_separation >= required_min_separation
```

Failure is an `EventThroughputError`, not an `EventCausalityError`. The compiler never silently drops
activations to satisfy a slow recurrence.

For inferred periodic clocks, the compiler may instead enlarge the period until the recurrence is
physically feasible.

## Periodic state timing

Ordinary state dependencies still use same-index semantics and therefore union connected registers
into one periodic clock domain. Independent components may retain different periods.

For a periodic dependency from source offset `r` to target commit offset `c`, physical logic latency
`L`, and shared period `P`, the phase constraint is

```text
phi_target >= phi_source + (r - c - 1) * P + L + 1
```

For the common recurrence

```text
S[k] -- L ticks of logic --> S[k+1]
```

this gives

```text
P >= L + 1
```

where the final tick is the state-writing combinator stage. Multicycle periodic domains synthesize a
modulo-`P` clock and gate state commits so intermediate physical ticks retain memory.

## Output materialization

Internal flows are sparse; Factorio wires are dense. Every exported flow therefore has a boundary
materialization policy:

- `HOLD`: retain the most recent activation value;
- `ZERO`: emit additive zero between activations;
- `VALID`: emit payload plus a separate presence signal.

Defaults are conceptually Level -> HOLD, additive Event -> ZERO, and general Event -> VALID. Explicit
policies override these defaults. Materialization changes only the circuit boundary; it does not
change internal Flow semantics.

For VALID outputs, payload and valid are physically phase-aligned. This property is tested across
irregular clocks, derived clocks, stateful bridges, zero payloads, and empty vector occurrences.

## Current implementation boundary

The clocked Event path reaches the same Abstract Physical IR, synthesis, layout, and blueprint
serialization pipeline as Level circuits. It currently covers the milestone vocabulary:

```text
external Level/Event sources
flow-local occurrence offsets
SampleOn
GateClock
EventMerge
SumInto
HoldInto
Event-triggered Freeze state
direct additive Event accumulator state
HOLD / ZERO / VALID outputs
```

The compiler intentionally remains conservative beyond that slice. Unsupported state-update shapes,
ambiguous crossings, and physical features requiring a true queue/FIFO produce compile-time errors.
Experimental Factorio entity capabilities are also not silently assumed by these semantics.
