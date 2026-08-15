# Architecture

The compiler has one semantic frontend and two timing/lowering lanes that converge on the same
Abstract Physical IR, synthesis, layout, and blueprint pipeline.

```text
ordinary Python elaboration
        ↓
symbolic frontend
  Circuit / Level inputs / Event inputs / expressions / state
        ↓
semantic CircuitModule
  - scalar/vector payload shape
  - Level/Event modality
  - clocks and occurrence offsets
  - explicit state transitions
  - explicit clock crossings
        ↓
logical analysis
  - causality
  - periodic state domains
  - Event clock contracts / required separation
        ↓
physical lowering
  Level lane                     Event lane
  - periodic phases              - payload + valid
  - inferred periods             - derived clocks
  - periodic state gates         - clock bridges
                                 - Event state
        ↓                         ↓
        Abstract Physical IR
  - exact target combinators
  - abstract signals and electrical nets
  - compatibility/conflict metadata
        ↓
physical synthesis
  - concrete signal allocation
  - red/green assignment
  - placement and reach-safe wiring
        ↓
Layout
        ↓
blueprint serialization
```

The abstract physical IR is target-specific. It exists so signal allocation, electrical-net choices,
and placement can be optimized jointly during physical synthesis.

## Symbolic frontend

Python runs once as elaboration. Symbolic operators construct immutable logical stream/state objects.

- `Circuit.input(name)` and `Circuit.signals(name)` create Level sources.
- `Circuit.event(name)` and `Circuit.signal_event(name)` create Event sources with explicit clocks.
- input/register `.sample()` is the compatibility observation API.
- expression `.step(n)` is flow-local occurrence reindexing and does not advance a global clock.
- the older `Circuit.step(n)` cursor remains only for compatibility with existing Level/state code.
- scalar/vector operators create logical expressions without exposing physical execution ticks.
- state objects create explicit read/update transition records.
- `Circuit.tick()` remains reserved for future explicit physical-tick constraints.

A canonical `Flow` records payload shape, Level/Event modality, base clock, and logical occurrence
offset. Derived expressions on one clock compose ordinarily; cross-clock behavior must be represented
by an explicit bridge or clock operation.

## Logical causality and timing

Logical causality and target timing are separate analyses.

A dependency edge records logical displacement independently from physical combinator latency. Every
feedback cycle must contain strict logical advance. Thus a positive-latency same-occurrence cycle is
rejected as noncausal even if a slower physical schedule might otherwise seem tempting.

Periodic state domains retain the established difference-constraint timing model. For source offset
`r`, target commit offset `c`, latency `L`, and period `P`:

```text
phi_target >= phi_source + (r - c - 1) * P + L + 1
```

The compiler chooses the smallest feasible period for inferred periodic domains.

Event clocks instead carry `guaranteed_min_separation`. Event-driven state derives a
`required_min_separation`; direct realization is legal only when the clock guarantee satisfies the
requirement. Failure is a throughput error rather than a causality error, and the compiler never
silently discards Event occurrences to make a recurrence fit.

## Event lowering stack

Physical Event lowering is deliberately layered so each extension reuses the previous invariant:

```text
ClockedPhysicalLowerer
  payload/valid alignment + output materialization
        ↓
StatefulClockedPhysicalLowerer
  Event Freeze state + direct SumInto topology
        ↓
DerivedClockPhysicalLowerer
  GateClock + EventMerge + derived SumInto
        ↓
ClockBridgePhysicalLowerer
  common bridge phases for derived HoldInto
        ↓
OccurrenceReindexPhysicalLowerer
  physical Event .step(n) tails
        ↓
EventAccumulatorPhysicalLowerer
  direct additive Event accumulator fusion
```

This inheritance is an implementation organization, not additional semantic layers.

### Payload and valid

Every external Event has a payload path and a separate valid/activation path. Payload computation may
run speculatively between occurrences; valid determines semantic presence. Zero scalar and empty
vector payloads therefore remain distinguishable from absence.

Same-clock combinational logic propagates payload normally and delays/shares the valid token to the
payload phase. `SampleOn` similarly evaluates the Level path continuously while delaying the target
activation token to the snapshot result phase.

### Derived clocks

`GateClock` filters a parent valid token using an aligned predicate. `EventMerge` forms an additive
union: simultaneous payloads add while simultaneous parent activations coalesce to one valid token.
Derived clock realizations are internal compiler nets and do not create extra external ports.

### Stateful bridges

`SumInto` uses a packed accumulator/snapshot topology with right-closed interval semantics:

```text
(previous target, current target]
```

A source occurrence simultaneous with a target is included.

`HoldInto` is strict-prior: simultaneous source/target means the target reads the old held value and
the new source becomes visible only later. Although the frontend elaborates HoldInto through hidden
Freeze state plus `SampleOn`, the physical bridge lowerer recognizes the relation and gives source
and target one shared execution phase. The target re-observes the live memory net at that phase;
it does not delay a stale earlier memory sample.

### Flow-local Event reindexing

A positive Event `.step(n)` means the same Event stream after dropping its first `n` occurrences. The
physical backend realizes this with one shared occurrence counter per base clock and a threshold/latch
per requested tail offset. Valid is suppressed during the prefix; payload computation remains
speculative and is phase-aligned afterward. No game-tick delay or future-payload prediction is
introduced.

### Direct Event state fusion

An ordinary accumulator with one unconditional Event `add` transition is lowered without an
intermediate `SumInto`: the Event vector is valid-gated to additive zero and connected directly to
the destination feedback accumulator. This is the current concrete bridge/state-fusion case.

More general Event accumulator programs—multiple Event transitions, Event clears, or arbitrary
conditional forms—remain outside the implemented physical subset and are rejected explicitly.

## Output materialization

Internal clocked flows are sparse while Factorio wires are dense. Circuit outputs therefore carry an
explicit or inferred materialization policy:

- `HOLD`: retain the latest activation value;
- `ZERO`: emit zero/empty between activations;
- `VALID`: export payload plus a phase-aligned presence signal.

The policies are boundary realization only; they do not alter internal Flow semantics.

## Abstract Physical IR

`ir/abstract_physical.py` represents exact Factorio combinator behavior while keeping late physical
resources unresolved. `AbstractSignal` is a compiler signal-lane variable rather than a concrete
`SignalId`; `AbstractNet` is an electrical-connectivity requirement with no chosen red/green color.

Nets distinguish compiler-allocated lanes, user-fixed concrete lanes, and runtime-open vectors.
`SignalConflict`, `SignalAlias`, and `NetConflict` express allocation/electrical constraints without
premature signal or wire-color choices.

Both Level and Event lowering produce this same IR.

## Physical synthesis and Layout

Physical synthesis jointly chooses:

- concrete Factorio signal identities;
- compatible net merges;
- red/green allocation;
- final placement and reach-safe wiring.

Its output is `Layout`. Blueprint generation is downstream serialization only; it does not choose
geometry or wiring.

`compile_circuit(...)` is the canonical path for both Level and the implemented Event subset.
`compile_abstract_circuit(...)` remains a compatibility alias. `compiler_legacy` is only a P=1
comparison/debugging oracle and is not the clocked Event backend.

## Reference simulation as the semantic oracle

`simulate_events(...)` executes irregular semantic schedules without physical pipeline latency. The
physical Event integration suite compares phase-shifted Factorio-circuit simulation against this
reference, including zero/empty payload presence, derived clocks, stateful bridges, occurrence
reindexing, and the multi-rate event-ledger benchmark.

This separation is intentional: semantic schedules define what the circuit means; physical lowering
is correct when its delayed dense wire trace materializes the same logical result.
