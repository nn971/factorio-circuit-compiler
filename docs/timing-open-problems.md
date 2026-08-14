# Timing open problems

## Triggered logical domains and pulse capture

The current multicycle scheduler infers a minimum physical initiation interval `P` for each logical clock domain and realizes it as a periodic cadence. This is sufficient for continuously sampled/level-like inputs, but it can miss short physical pulses that occur between logical activations.

Example: a FIFO domain with `P=5` samples an external `push` pulse only on its periodic activation ticks. A one-game-tick pulse between those activations may therefore disappear completely from the logical stream.

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

Do not block the autonomous-market prototype on this yet. Continue using level/held signals or an explicit environment handshake for events that must survive a multicycle controller. Return to this design before treating arbitrary one-tick external pulses as reliable inputs to `P>1` domains.
