# Clocked Flow Semantics Milestone Closeout

## Status

**Implementation complete on `agent/physical-clock-lowering`.**

This file is the implementation closeout for `docs/clocked-flow-semantics-milestone.md`. The original
milestone document remains the design/history record; this document records what is now executable,
what tests establish the completion criteria, and which deliberately broader features remain future
work.

The implemented clocked Event subset reaches the ordinary compiler backend:

```text
semantic CircuitModule
        ↓
clocked causality / timing
        ↓
Event physical lowering
        ↓
Abstract Physical IR
        ↓
physical synthesis + Layout
        ↓
blueprint serialization
```

`simulate_events(...)` remains the independent semantic oracle used to validate the physical route.

## Completion criteria

### 1. Every logical flow has an explicit clock and occurrence offset — complete

Canonical `Flow` metadata records modality, payload shape, base clock, and logical occurrence offset.
Frontend normalization/reindexing carries these fields through scalar/vector expressions.

### 2. Global semantic cursor mutation is no longer fundamental — complete

Expression `.step(n)` is the semantic reindexing operation. The older `Circuit.step()` cursor remains
only as compatibility syntax for existing Level/state elaboration.

### 3. `.step()` is pure logical reindexing — complete

Level expressions reindex sample leaves. Event `.step(n)` denotes the tail of the occurrence stream:
the first `n` occurrences are suppressed, with no conversion to `n` physical ticks and no inserted
payload register.

Physical Event tails are implemented by `OccurrenceReindexPhysicalLowerer`, using one occurrence
counter per base clock plus threshold/latch valid gates. Regression coverage:

- `tests/frontend/test_flow_local_step.py`
- `tests/integration/test_occurrence_reindex_physical.py`

### 4. Same-occurrence combinational feedback is rejected — complete

Logical causality analysis is independent of physical latency and rejects zero-logical-advance
cycles. Positive-latency feed-forward paths remain legal.

Coverage lives under `tests/timing/test_causality.py` and
`tests/timing/test_logical_causality_api.py`.

### 5. Logical advance derives physical minimum separation — complete

State timing derives recurrence latency and Event `required_min_separation` rather than conflating
logical causality with target throughput.

### 6. Inferred clocks can be slowed — complete

Periodic inferred domains enlarge their period to the smallest feasible value. Covered by clock
contract/domain and representative-state timing tests.

### 7. External Event clocks are checked against their guarantee — complete

External/derived Event clocks carry `guaranteed_min_separation`; physical/reference compilation
validates it against the derived requirement.

### 8. Throughput violations are distinct from causality errors — complete

`EventThroughputError` reports insufficient arrival spacing after logical causality succeeds.
`EventCausalityError` remains reserved for logically invalid recurrence structure.

### 9. Stateful cross-clock information preservation is explicit — complete

The implemented vocabulary contains explicit `SumInto` and `HoldInto` bridges. Ordinary expressions
do not silently invent history-preserving re-clocking.

### 10. Identical bridges are shared — complete

Frontend bridge construction is interned and physical derived-clock/payload realizations are cached.
The multi-rate ledger checks that one shared `EventMerge` physical payload feeds several downstream
crossings rather than being rebuilt per use.

### 11. Vector bridges remain packed — complete

Event vectors cross through runtime-open vector nets and whole-vector arithmetic/decider behavior;
there is no accumulator-per-signal-lane lowering.

### 12. Additive Event sources merge before accumulation — complete

`EventMerge` produces one additive packed producer stream which may then feed multiple `SumInto`
bridges. The flagship ledger asserts the three-way merge uses exactly `N-1` vector-add stages once,
not once per reporting bridge.

### 13. Bridge state can fuse with compatible logical state — complete for the milestone case

An ordinary `AccumulatorReg` with one unconditional Event `add` transition absorbs the valid-gated
Event payload directly. No intermediate `SumInto` is emitted.

Coverage:

- `tests/integration/test_event_accumulator_physical.py`
- the lifetime total in `tests/integration/test_multi_rate_event_ledger.py`

This establishes the intended optimization principle without claiming a generic Event-state
optimizer. Multiple Event transitions, Event clears, and arbitrary conditional accumulator forms are
still unsupported physically.

### 14. Exported sparse flows have HOLD/ZERO/VALID materialization — complete

Boundary materialization is explicit and payload/valid phases are aligned. Coverage includes general
and additive Events, irregular clocks, zero-valued scalar payloads, and empty-vector occurrences.

### 15. Physical simulation matches a pure semantic reference on irregular schedules — complete

`tests/integration/test_multi_rate_event_ledger.py` is the flagship acceptance test. It combines:

- three independent irregular vector Event producers;
- simultaneous producer occurrences;
- one shared `EventMerge`;
- fast, gated-slow, and audit reporting clocks;
- three `SumInto` crossings;
- a direct lifetime Event accumulator;
- VALID report outputs and a held lifetime Level output.

The test compares each reporting output timestamp-by-timestamp against `simulate_events(...)` after
accounting for physical output phase, and separately checks the final lifetime state and sharing
structure.

## Physical semantics fixed during implementation

The milestone implementation established several backend conventions that are now part of the
compiler model.

### Event representation

```text
Event = payload path + valid/activation path
```

Payload may be speculative between occurrences; valid denotes semantic presence. Payload and valid
are aligned before an Event consumer or external VALID boundary.

### Stateful bridge phase

A stateful cross-clock bridge chooses one common physical execution phase. Source payload/source
valid and target valid are delayed to that phase from their respective semantic occurrences before
interacting with bridge state.

### HoldInto boundary

`HoldInto` is strict-prior. A source occurrence simultaneous with a target occurrence is not observed
by that target; it becomes visible to a later target.

### SumInto boundary

`SumInto` is right-closed:

```text
(previous target, current target]
```

A simultaneous source contribution is included in the current snapshot, after which the accumulator
starts the next interval empty.

### Event `.step(n)` boundary

Positive Event reindexing removes an occurrence prefix. It does not require a FIFO because the
semantic value after reindexing is still the current payload of each surviving occurrence.

## Deliberately remaining work

The following are **not** blockers for this milestone and should not be inferred from the supported
subset:

- a general FIFO/queue primitive for overloaded or explicitly buffered Event clocks;
- ready/valid backpressure protocols with external devices;
- multiple independent Event updates to one ordinary accumulator;
- Event accumulator clear/replace combinations and arbitrary conditional update forms;
- richer burst-arrival clock contracts beyond minimum separation;
- a general Event/state fusion optimizer beyond the direct additive case;
- retirement of every legacy sampled/source wrapper or the compatibility `Circuit.step()` API;
- capability profiles for stable versus experimental Factorio entity features.

Those belong to subsequent compiler/device-protocol milestones.

## Consequence for the autonomous market

The original motivation—short-lived environment events interacting with slower logical control—now
has a semantic substrate rather than requiring an ad-hoc periodic-sampling workaround. External
completion/pulse streams can be represented as Events, Level state can be sampled on their clocks,
Event history can be explicitly preserved across slower clocks, and the compiler can reject arrival
rates that exceed a recurrence's proved throughput.

Migrating the existing in-game autonomous-market controller to an Event-oriented device protocol is
a separate application step, not unfinished clock semantics.
