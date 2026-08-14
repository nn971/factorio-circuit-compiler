# Timing open problems

## Semantic Event/SampleOn reference boundary

The compiler now has a semantic/reference-only Event path: declared Event schedules can trigger
`FreezeReg.capture_on(...)` in `simulate_events(...)` with deterministic same-timestamp snapshots and
REJECT-only declared-throughput validation. This path does not produce physical pulses or blueprints.

Event captures and SampleOn crossings are outside `StateTimingPlan`. Frontend elaboration constructs
the semantic `CircuitModule`; a Level/physical route then raises `EventCompilationError` before
state-timing analysis or semantic-to-physical lowering. `simulate_events()` and reference
materialization are the supported path; no abstract physical IR, synthesis, blueprint generation, or
Level simulation follows.

Physical pulse capture, buffering/FIFO behavior, latching, ready/handshake protocols, periodic/Event
mixing, and output alignment remain open. No implementation may silently drop or retain an Event to
make a physical schedule fit.

Phase 4 closes only the semantic observation boundary: `SampleOn` is a non-expression reference that
samples an existing raw Level at a declared Event activation, and the reference runner exposes explicit
HOLD/ZERO/VALID materialization of Event and SampleOn values over a validated half-open timestamp
domain. The rows and optional VALID presence flag are independent reference data, not hardware. None
of these policies imply physical cadence, storage, pulse retention, bridges, activation gates, or
Factorio valid wiring; all Event/SampleOn modules remain rejected by physical and Level-only routes.

Event clock taxonomy, periodic/Event mixing, SumInto/HoldInto/EventMerge, physical bridges, bridge
CSE/packing, physical output policies, valid wiring, and pulse buffering remain deferred.

## Triggered logical domains and pulse capture

The current multicycle scheduler infers a minimum physical initiation interval `P` for each logical clock domain and realizes it as a periodic cadence. This is sufficient for continuously sampled/level-like inputs, but it can miss short physical pulses that occur between logical activations.

Example: a FIFO domain with `P=5` samples an external `push` pulse only on its periodic activation ticks. A one-game-tick pulse between those activations may therefore disappear completely from the logical stream.

The same issue can affect short-lived levels. The autonomous-market worker now uses an assembler's `Read working` output rather than a synthetic completion signal. If an entire craft's working interval begins and ends between two logical observations, a slow controller can miss that interval just as it can miss a pulse. The first prototype therefore assumes the worker's working interval is long enough to be observed; this is not a general solution.

### Candidate semantic direction

Interpret the inferred `P` as a **minimum spacing between accepted logical activations**, not necessarily as a rigid period:

```text
T(k + 1) - T(k) >= P
```

A future triggered domain could accept the first activation event while ready, snapshot its scalar/vector inputs at that physical tick, then suppress further activations until the minimum interval has elapsed.

This suggests an explicit activation predicate rather than trying to infer which external values are "meaningful". For example, a FIFO might conceptually use:

```python
activate = push_requested | pop_requested
```

The activation predicate answers "should a new logical reaction start?"; it is distinct from whether the resulting state transition is accepted or changes state.

### Information-loss boundary

If another pulse arrives while the domain is busy, no implementation can both exceed the domain's throughput limit and preserve arbitrary events without extra state. The semantics must eventually choose or expose one of:

- drop/suppress activation events while busy;
- require a `valid`/`ready`-style handshake so the producer holds the event/payload;
- add an explicit pending latch/FIFO/event accumulator.

A hidden one-event buffer should not be introduced implicitly into every slow domain.

### Snapshot requirement

Triggered activation also implies physical input capture. If a logical sample is used several combinator ticks after activation, it cannot remain attached to a live external wire whose value may have changed. The compiler would need to synthesize appropriate scalar/vector sample-and-hold storage for the activated reaction.

### Deferred decision

Do not block the autonomous-market prototype on this yet. Continue using level/held signals or an explicit environment handshake for events that must survive a multicycle controller. Return to this design before treating arbitrary one-tick external pulses or short-lived working levels as reliable inputs to `P>1` domains.
