# Shared delay-bus design decisions

Status: design decisions for the redesign described in `shared-delay-bus-plan.md`.

This note resolves the three questions that were deliberately left open in the handoff plan:

1. what an abstract signal lane means before concrete signal allocation;
2. when a later use may choose a fresh Level observation instead of preserving an earlier token;
3. how observation/reuse choices interact with exact-transport and delay-bus planning.

The experimental `TemporalPlanLowerer` remains regression material. These decisions describe the
new contract rather than committing the compiler to that implementation.

## 1. Abstract lanes are unique logical physical resources

Keep the existing conceptual pipeline:

```text
semantic value/token
    -> one or more abstract lane instances
    -> carrier/net occupancy
    -> concrete Factorio signal allocation
    -> red/green electrical realization
```

`AbstractSignal` should be interpreted as an **abstract lane instance**, not as a concrete Factorio
signal name and not as the identity of a semantic value.

An abstract lane instance has a unique compiler id. Those ids should not be recycled. Reusing ids
would make collision reasoning depend on construction order and would blur the distinction between
semantic identity and physical resource allocation.

The reusable resource is the eventual concrete `SignalId`. Physical synthesis may assign the same
Factorio signal identity to two different abstract lanes whenever the interference graph proves that
they cannot collide. The current DSATUR allocator already has exactly this shape: abstract lanes that
share a synthesized electrical group conflict, while disconnected non-conflicting lanes may receive
the same concrete signal.

Consequences:

- lane allocation remains late;
- semantic lowering never chooses `signal-A`, `signal-B`, ... merely to reserve bus slots;
- two realizations of the same semantic token may use different abstract lanes when isolation or
  rematerialization is profitable;
- `SignalAlias` is reserved for cases that truly require two abstract lanes to receive one concrete
  signal identity; semantic equality alone does not imply aliasing;
- explicit `SignalConflict` remains useful for interference that is not already implied by sharing an
  electrical carrier.

## 2. Carrier occupancy, not semantic identity, determines collisions

A carrier is a physical network fragment that can contain one or more abstract lanes. In the current
Abstract Physical IR, an `AbstractNet.signals` tuple is already a conservative static description of
which allocated lanes may coexist on that net.

For the first accepted shared scalar bus, keep this static rule:

> If two abstract lanes can coexist on one synthesized carrier, they require distinct concrete
> Factorio signal identities.

This deliberately leaves some possible temporal signal-name reuse on the table. A continuous
`Each + 0 -> Each` bus stage does not automatically erase a lane when its last semantic consumer has
finished. Once a lane has joined such a trunk, stale values can continue through later stages.
Therefore two semantic lifetimes that merely do not overlap at their consumers are not sufficient to
justify reusing one concrete signal on the same trunk.

Concrete signal reuse is immediately safe across disconnected carriers/buses because the existing
interference graph can color those abstract lane instances identically.

A future carrier that can explicitly retire, filter, or remap lanes may add phase-aware occupancy
metadata. That extension should be motivated by a real implementation primitive; the first redesign
should not add interval coloring while the physical bus itself cannot enforce interval retirement.

## 3. Distinguish same-token validity from fresh observability

Two independent proofs can make a later Level use free.

### Same-token validity

A physical representation can be proved to carry the **same logical token** through an interval.
Constants and held state are the canonical examples.

```text
reuse_at(value, phase)
```

changes only the view phase and preserves semantic token identity.

### Fresh observability

A physical representation can instead be proved to keep tracking a live Level. Observing it later is
allowed to select the value present at that later physical tick.

```text
observe_at(value, phase)
```

is therefore a freshness choice, not token-preserving transport.

Under `SamplingPolicy.ALAP`, this property propagates through ordinary feed-forward Level logic. A
Factorio combinator continuously reevaluates, so a comparison/arithmetic/vector result remains
freshly observable while all of the physical inputs needed for that reevaluation remain available.
Mixing a live input with state that is held for the whole occurrence is a common example.

This propagation is deliberately separate from `ValidityWindow`: one describes the interval over
which a representation denotes one chosen token, while the other describes the interval over which
the representation may intentionally denote fresh later values.

## 4. Exact transport is the freeze boundary

Once a particular token matters, the compiler must establish that boundary explicitly.

```text
live/re-observable Level
        |
    observe_at(t)       choose the token/value at t
        |
    exact transport     preserve that chosen token
        v
      later use
```

Calling `exact_delay_to` means that the caller cares about the chosen token. The resulting transport
must not inherit the source's fresh-observation freedom. In other words, exact transport acts as a
one-way **freeze boundary**.

This keeps the useful compatibility behavior of `delay_to` without making its semantics mysterious:

```text
delay_to(value, target)
    1. same-token reuse, if the validity proof covers target;
    2. fresh observation, if the configured policy allows it and observability covers target;
    3. exact transport otherwise.
```

Code that requires step-start coherence, a specific oracle draw, or any other chosen-token guarantee
continues to call the explicit exact operation rather than relying on this dispatcher.

## 5. Transport demand is formed after observation/reuse elimination

The delay-bus planner should not receive every temporal phase gap and decide afterward which ones were
real transports. Instead temporal alignment should classify each later use first:

```text
same-token validity covers use
    -> REUSE            cost 0

fresh observation permitted at use
    -> OBSERVE_AT       cost 0, intentionally later semantic value

chosen token must survive to use
    -> EXACT_TRANSPORT  transport demand [start, end]
```

Only `EXACT_TRANSPORT` demands are candidates for private identity chains or shared delay buses.

This ordering makes `observe_at` and `transport_to` first-class optimizer alternatives while keeping
the shared bus semantically simple: the bus implements exact transport only.

For a live-derived value with several consumers, different consumers may observe the same physical
representation at different phases under ALAP. No transport lifetime exists merely because those
consumer phases differ.

For an exact value with several consumers, the transport planner should form one lifetime reaching
the latest exact consumer and then compare legal realizations:

```text
private exact chain
shared exact bus
rematerialization / isolation, when later implemented
```

## 6. Coherent observation is an explicit stronger contract

There are uses where several later consumers must agree on one observation even though the source is
live. That is stronger than ordinary ALAP freshness.

The compiler should represent such a requirement explicitly as a snapshot/coherence boundary:
choose one observation phase, then exact-transport that chosen token to the remaining consumers.

A generic ALAP optimizer should not silently introduce this coherence requirement. It changes the
program's allowed physical observations and can add transport that the ordinary ALAP policy does not
need.

The existing experimental `LiveSourceObservation` plan object remains useful as a prototype of this
stronger contract and as regression material, but it should not define the default meaning of ALAP.

## 7. Delay-bus optimizer contract

The future bus optimizer should consume explicit exact-transport requests of the form

```text
TransportDemand
    semantic/token identity
    abstract source lane realization
    start phase
    required tap/end phases
    carrier/isolation constraints
```

and produce a transport realization plan:

```text
PrivateChain
or
SharedBus(bus_id, abstract_bus_lane, ingress, taps/egress)
```

The `abstract_bus_lane` is an abstract lane instance. Its concrete Factorio signal is still chosen by
physical synthesis.

Before assigning two demands to one bus, compatibility analysis must establish lane, temporal,
electrical, and consumer safety. The first safe implementation should continue to assume that an
introduced bus lane occupies the trunk through the bus end unless an explicit retirement primitive
is emitted.

The bus cost is then based on the actual realization:

- shared middle stages;
- required ingress/isolation;
- required egress/extraction;
- private short transports that are cheaper than joining the bus;
- remaps/duplication required by carrier constraints.

## 8. Immediate implementation consequences

The redesign can proceed incrementally:

1. propagate ALAP fresh-observability through ordinary feed-forward Level lowering;
2. add focused tests that distinguish later observation from exact token transport;
3. expose the same classification in temporal analysis so the optimizer removes free
   `REUSE`/`OBSERVE_AT` gaps before forming bus candidates;
4. keep `AbstractSignal` ids unique and rely on final interference coloring for concrete signal-name
   reuse;
5. define the safe shared-carrier primitive and ingress/egress requirements;
6. only then replace eligible exact private chains with the new bus planner.

This keeps the correctness boundary simple: late observation may choose a different Level value,
while every shared delay-bus lane preserves exactly the token handed to it.
