# Shared scalar delay-bus redesign plan

Status: design handoff after temporal-alignment cleanup. The shared-bus implementation remains
experimental and is **not** an accepted compiler path.

This document defines the starting point for a fresh implementation context. It deliberately does
not commit to the current `TemporalPlanLowerer` / `IsolatedTemporalPlanLowerer` representation. Those
experiments demonstrated that shared transport can materially reduce delay combinators, but they also
exposed missing contracts between temporal alignment, abstract physical values, and electrical net
synthesis.

Read first:

1. `docs/data-contract.md`
2. `docs/compiler-pipeline.md`
3. `docs/temporal-alignment.md`
4. `docs/temporal-lowering-milestone.md`

## 1. Settled temporal contract

The next delay-bus implementation must build on the explicit distinction introduced by the alignment
cleanup.

### Level reuse

If a realized Level is already proved to carry the same logical token at a later phase, the compiler
may view the same physical representation at that phase with no hardware.

### Late observation

A live external Level source may be observed at a later phase. This chooses a later token; it does not
preserve the token that happened to be present earlier.

Use the explicit observation operations:

```text
observe_scalar_at(...)
observe_vector_at(...)
```

### Exact transport

Once a particular token has been chosen, preserving it until a later phase is exact transport.

Use:

```text
exact_delay_to(...)
exact_delay_vector_to(...)
```

These operations are intentionally independent of late observation and settling reuse. A future
shared scalar bus should be an alternative implementation of **exact scalar transport**, not another
meaning of `delay_to()`.

### HOLD

A HOLD captures a token and keeps it available across an interval. It is stateful and is not the same
operation as exact tick-by-tick transport.

### Event/pulse transport

Event/pulse physical transport is a separate future concern. Do not reuse Level persistence or late
observation rules for pulses. In particular, a future pulse transport/HOLD API should state whether it
preserves occurrence timing, captures a pulse into state, or converts it to a Level.

## 2. Why restart the delay-bus design

The accepted random-food ALAP Snake baseline has 698 implementation combinators at period 65, with
421 phase-delay combinators: 403 scalar and 18 vector. This makes scalar exact transport the dominant
remaining local cost.

The experimental temporal buses demonstrated that multiplexed `Each + 0 -> Each` transport can reduce
that cost substantially. The earliest candidate fell to 454 implementation combinators. Later,
progressively safer physical realizations reached roughly 486-520 combinators while preserving the
same period. Therefore the optimization is worth keeping.

However, the experiments exposed several independent-looking failures that share one architectural
cause: the existing abstract physical representation does not explicitly state the contracts needed
by a multi-lane transport fabric.

Observed lessons include:

- direct bus ingress can electrically back-propagate unrelated lanes onto producer nets;
- relying on physical synthesis to merge many ingress nets can make red/green constraints
  non-bipartite;
- a shared trunk connected directly to unrelated consumers can impose incompatible wire-color
  relationships;
- one-tick lifetimes have no shareable middle transport and should normally remain private;
- structural metadata such as `clean_single_lane` is too coarse to express all of the above;
- an idealized bus-stage objective underprices ingress/egress isolation when those conversions are
  physically required.

The experimental lowerers and tests should therefore be treated as evidence and regression material,
not as the architecture to polish indefinitely.

## 3. Main design question: what does Abstract Physical IR carry?

Before implementing a new bus, decide how a scalar value occupies a Factorio signal lane in Abstract
Physical IR.

The promising direction is to distinguish at least these concepts:

```text
semantic scalar/token
    -> abstract signal lane
    -> carrier containing one or more lanes
    -> final red/green electrical realization
```

Today `RealizedValue(signal, net, phase, clean_single_lane)` mixes several of these concerns. The next
design should decide whether an abstract variable owns one lane directly and whether carrier/net
objects explicitly know which lanes they transport.

Questions to settle:

- Is one abstract lane a long-lived identity for one semantic value, or a reusable physical resource
  whose temporal lifetime is explicit?
- Can the same semantic token have multiple lane realizations when isolation/rematerialization is
  profitable?
- Is an `AbstractNet` a final electrical-equivalence class, or only a connectivity fragment that
  synthesis may coalesce?
- Which properties belong to the value/lane and which belong to the carrier/net?
- Should scalar-only versus multiplexed-carrier safety be represented by types/metadata rather than
  the single `clean_single_lane` boolean?
- Which compatibility facts must be explicit before physical synthesis is allowed to merge nets?

Do not change this representation merely to fit the current bus prototype. First define invariants
that also make ordinary scalar operations, packed `Each` operations, vectors, and red/green synthesis
clearer.

## 4. Delay-bus semantic contract

A shared scalar delay bus should be defined as an implementation of exact transport for several
independent scalar tokens.

For lane `x` transported from phase `s` to phase `e`:

```text
bus_transport(x, s, e)
```

must be observationally equivalent to the ordinary private exact-delay chain for `x` over the same
interval.

The implementation may multiplex several lanes through `Each + 0 -> Each` stages only when this does
not alter:

- token identity;
- observation phase;
- arithmetic value;
- visibility of unrelated signals at producer or consumer networks;
- red/green feasibility of the final circuit.

A useful conceptual shape remains:

```text
producer
   |
possible isolation / lane conversion
   |
shared multiplexed transport
   |
possible isolation / lane extraction
   |
consumer
```

But ingress and egress must not automatically cost one combinator each. The design should derive when
conversion is necessary from explicit carrier/value invariants.

## 5. Interference must be first-class

Bus compatibility must be established before or together with bus selection, rather than repaired
opportunistically after CP-SAT chooses a group.

At minimum consider four kinds of interference:

### Lane interference

Two simultaneously present values cannot use the same concrete Factorio signal identity when their
counts must remain distinguishable.

### Temporal interference

One physical lane cannot represent two different logical tokens at the same physical tick unless the
semantics proves they are identical.

### Electrical interference

Joining a value to a multiplexed carrier must not expose unrelated lanes back onto a producer network
or otherwise change another existing consumer's input network.

### Consumer interference

A multiplexed carrier cannot be connected directly to a consumer whose operation assumes a private
scalar lane or whose red/green relationship conflicts with the shared carrier.

The future compatibility analysis should produce explainable reasons, not merely a boolean, so failed
packing decisions can be diagnosed.

## 6. Separation of optimization and realization

Keep these responsibilities distinct:

```text
temporal analysis
    determines which exact tokens must survive over which intervals

transport planner
    chooses private chains versus legal shared carriers

abstract physical lowering
    emits the chosen transport primitive with explicit constraints

physical synthesis
    allocates concrete Factorio signal identities, red/green colors, placement and routing
```

Physical synthesis should not need to understand clocks, logical occurrences, late observation, or
why a value was delayed. Conversely, the transport planner must not assume synthesis can silently
merge connectivity fragments in ways that are semantically harmless unless that compatibility is
part of the Abstract Physical IR contract.

## 7. Cost model

Do not reuse the current idealized objective unchanged.

For a proposed bus group, count the actual physical realization implied by the chosen carrier model:

- shared middle stages;
- required lane ingress/isolation conversions;
- required lane egress/extraction conversions;
- private one-tick or short transports that have no profitable shared middle;
- any additional constraints that force duplicated transport.

The optimizer should compare this cost directly against private exact-delay chains. A lane should use
a bus only when it is cheaper under the real model.

Keep computation placement fixed to the already validated production ALAP schedule during the first
new bus milestone. Global temporal placement should be re-enabled only after shared exact transport is
independently validated.

## 8. Validation strategy before Snake

Do not use the full Snake blueprint as the first correctness test.

### Contract tests

Add small tests that establish:

- `observe_*_at` changes observation time without creating transport;
- `exact_delay_*_to` preserves the chosen token;
- Level-validity reuse creates no hardware;
- HOLD remains stateful and separate;
- a shared-bus transport is tick-for-tick equivalent to private exact transport.

### Bus primitive tests

Construct small deterministic circuits with several independent scalar traces and compare:

```text
private exact-delay reference
        ==
shared-bus realization
```

across random values, start phases, end phases, fanout patterns, and multiple consumers.

Test electrical invariants explicitly, including producer isolation, consumer isolation, signal-lane
coexistence, and red/green colorability.

### Small feedback test

Use a compact stateful circuit containing several delayed controls and feedback before returning to
Snake. Physical simulation should locate the first divergent tick automatically.

### Snake acceptance

Only after the primitive passes deterministic tests:

1. freeze production ALAP computation placement;
2. replace only eligible private scalar exact transport with the new bus planner;
3. generate the full random-food Snake;
4. verify startup, food appearance, movement, turning, eating, growth, collision and respawn behavior
   in Factorio;
5. only then record a new accepted baseline.

The accepted `random-food-alap-v1` result remains the correctness baseline until this happens.

## 9. Experimental-code cleanup

Do not delete the existing temporal bus code at the start of the redesign. It contains useful failure
cases and metrics. Use it to derive tests and compare implementations.

Once the new primitive is accepted:

- remove or clearly archive the obsolete experimental lowerer rather than maintaining two bus
  architectures;
- keep only generally useful temporal-hypergraph/optimizer machinery;
- update `docs/compiler-pipeline.md` if a new durable transport-planning stage is introduced;
- update benchmark baselines with rejected/accepted results that remain useful for regression.

## 10. Immediate next-context agenda

Start the fresh context with design, not code:

1. inspect `RealizedValue`, `AbstractSignal`, `AbstractNet`, operand semantics, net coalescing, and the
   DSATUR signal allocator together;
2. propose 2-3 concrete abstract lane/carrier representations;
3. evaluate each representation on ordinary scalar consumers, `Each` operations, vectors, and the
   red/green wire model;
4. choose the smallest representation that makes bus interference explicit;
5. specify one canonical shared exact-transport primitive and its cost;
6. build deterministic equivalence tests;
7. implement the bus planner/realizer only after those contracts are fixed.

The purpose of the next milestone is not merely to beat 520 combinators. It is to make shared scalar
transport a correctness-preserving compiler feature whose legality and cost can be reasoned about
before Factorio gameplay is used as the final acceptance test.

## Handoff validation state

At the time this document was written, the temporal-alignment refactor had reached commit
`026a44c51b542d92a5e5f84a1b84a7840c66adc2`. The latest focused run reported 21 passed, one skipped,
and one scalar sampling-policy assertion failure; that assertion was then corrected because it had
mistaken intentional startup exact transport for external-input transport. The focused suite should
be rerun before treating the alignment refactor as fully validated.
