# Data contract

This document defines the supported logical data model. Physical combinator timing and layout are compilation details described in `compiler-pipeline.md`.

## 1. Two time coordinates

The source program describes logical clock occurrences. Factorio realizes them with physical game ticks.

For a periodic clock `C` with period `P`, a value at occurrence `k` may be realized at physical time

```text
k * P + phase
```

For an irregular Event clock, occurrence times are `tau_C(0), tau_C(1), ...`; physical phase is latency measured from each occurrence. Feed-forward latency does not by itself reduce throughput because combinator pipelines overlap.

## 2. Flow

The canonical semantic coordinate is:

```text
Flow
    payload_shape    SCALAR | VECTOR
    modality         LEVEL | EVENT
    clock            structural Clock
    logical_offset   integer occurrence offset
```

Read a flow as `x_C[k + logical_offset]`. Physical phase is deliberately absent from `Flow`.

Clock identity is structural: `(identity, provenance)`. Timing knowledge such as `guaranteed_min_separation` is a contract about that clock, not part of its identity.

Clock provenance currently includes inferred periodic, fixed periodic, external Event, and derived clocks.

## 3. Level and Event

`Level[T]` is persistent information that may be sampled at a clock occurrence. Typical examples are circuit-network contents and held state.

`Event[T]` is a sequence of discrete occurrences carrying payloads. Presence is independent of payload value: scalar zero and an empty vector are valid present Event payloads.

A Level crossing naturally samples. An Event crossing must state how event history is preserved. This is why modality and clock are independent properties.

## 4. Ordinary expressions

Arithmetic, comparisons, selects, lane reads, and runtime-open vector operations preserve logical occurrence. Operands in one ordinary Event expression must already use a compatible structural clock and one occurrence offset.

Physical combinators add latency later. The semantic expression remains same-occurrence dataflow.

## 5. Logical reindexing

```python
later = value.step(n)
```

means the same flow at occurrence offset `+n`.

For Level expressions the reindex is pushed to their observation leaves. For Events, positive reindexing denotes the tail of the occurrence stream: the first `n` occurrences are suppressed and surviving payloads remain occurrence-local.

`.step()` is never a game-tick delay and never silently creates storage.

`Circuit.step()` remains a compatibility observation cursor for existing Level/state code. `Circuit.tick()` is reserved for future explicit physical scheduling.

## 6. State

State is a logical stream `S[k]`. One activation is atomic:

```text
observe occurrence payloads and Level/state inputs
    -> evaluate expressions
    -> determine updates
    -> commit S[k+1]
```

`AccumulatorReg` and `FreezeReg` are packed whole-vector state primitives. Reads and update calls preserve elaboration order; a read cannot split one compound state transition.

Periodic state and Event-driven state use the same logical transition idea. They differ in physical scheduling: periodic domains infer/check a period, while Event clocks provide an arrival-spacing contract.

## 7. Causality and throughput

Logical causality is independent of target latency. Every comparable-clock feedback cycle must contain strict logical advance. A zero-advance feedback cycle is rejected even if a slower physical schedule could be imagined.

After causality succeeds, physical timing derives feasibility requirements.

For periodic state, connected state registers share a domain period `P`; inferred clocks choose the smallest feasible period.

For Event-driven recurrence, analysis derives `required_min_separation` and checks

```text
guaranteed_min_separation >= required_min_separation
```

An insufficient arrival guarantee is a throughput error. The compiler does not drop Event occurrences to satisfy a slow recurrence.

## 8. Explicit clock operations

Ordinary expression regions are single-clock. Cross-clock behavior uses explicit operations.

### `sample_on(level, target_event)`

Sample the Level at each target occurrence. The result is an Event flow on the target clock.

### `gate_clock(parent, when=predicate)`

Create a subclock containing parent occurrences where the predicate is true. Gating removes activations, so the derived clock conservatively inherits the parent's minimum-separation guarantee.

### `event_merge(a, b, ...)`

Additive union of same-shaped Event sources. Simultaneous parent activations coalesce into one occurrence whose payload is the sum of simultaneous payloads. Unrelated merged sources conservatively use a one-tick minimum-separation guarantee.

Equivalent merges/bridges are shared where possible; vector payloads remain packed.

### `sum_into(source, target)`

Stateful additive crossing. At target occurrence `k`, emit the sum of source payloads in

```text
(previous_target, current_target]
```

A simultaneous source occurrence is included in the current target result.

### `hold_into(source, target)`

Stateful latest-value crossing. The target observes the latest strict-prior source value. If source and target activate simultaneously, the target sees the previously held value and the new source value becomes visible later.

## 9. Physical Event representation

An Event lowers to aligned physical channels:

```text
payload
valid / activation token
```

Payload logic may evaluate between semantic occurrences. `valid=1` denotes presence. Consumers and `VALID` outputs receive payload and valid at the same physical phase.

A feed-forward Event pipeline does not require a snapshot register merely because it takes several combinator ticks; payload and valid can be pipelined together. Storage appears only for real history, buffering, or recurrence.

## 10. Output materialization

Internal Event flows are sparse while Factorio wires are dense. Boundary materialization is explicit:

- `HOLD`: retain the latest present payload;
- `ZERO`: emit additive zero/empty value between occurrences;
- `VALID`: expose payload plus a companion presence output.

Current defaults are Level -> HOLD, additive `EventMerge`/`SumInto` -> ZERO, and other Events -> VALID. Materialization changes only the external boundary, not internal flow semantics.

## 11. Supported clocked subset

The physical compiler currently supports external Level/Event inputs, flow-local occurrence offsets, `SampleOn`, `GateClock`, `EventMerge`, `SumInto`, `HoldInto`, Event-triggered Freeze state, direct unconditional additive Event accumulation, and HOLD/ZERO/VALID outputs.

Queues/FIFOs for overloaded clocks, ready/valid backpressure, richer burst contracts, arbitrary multi-Event updates to one register, and general cross-clock state communication require explicit future abstractions. Unsupported shapes are rejected rather than approximated.
