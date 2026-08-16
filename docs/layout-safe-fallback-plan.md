# Deterministic safe layout fallback plan

## Motivation

The first Snake prototype exposed an important distinction:

> cheap placement is not necessarily cheap synthesis.

A one-dimensional row placement is trivial to compute, but it can turn many otherwise local circuit
connections into long physical edges. The current collision-aware router then spends substantial work
finding relay chains, including bounded half-tile graph searches. For a large circuit this can make a
"simple" fallback slower and less predictable than the optimized layout path.

A second Snake playtest exposed the complementary failure mode: a single deterministic greedy placement
can be fast to compute and still leave no collision-free route for one of the later physical
connections. The intermediate mitigation is now to retry greedy placement deterministically while
spending progressively more area: lower target fill and wider routing corridors on every failed
attempt. This improves robustness, but it remains heuristic because the existing router still searches
for relay positions and may exhaust its search.

We therefore still want a separate **safe fallback** whose goal is not compactness. Its priorities are:

1. deterministic behavior;
2. guaranteed termination for every circuit inside its declared supported physical subset;
3. no heuristic search, annealing, random restart, or routing backtracking;
4. wire-reach and entity-collision correctness by construction;
5. runtime proportional to the amount of structure it actually emits;
6. willingness to consume very large amounts of map area and relay entities.

This is a later layout milestone. The current Snake path uses spacious deterministic greedy retries as
an intermediate robustness measure.

## Complexity target

A strict `O(V + E)` guarantee, where `V` is implementation entities and `E` is logical physical
connections, is probably the wrong target for a bounded-reach blueprint backend.

If two endpoints are physically far apart, Factorio requires intermediate relay entities. Let `R` be
the number of relay entities in the emitted fallback layout. Merely constructing and serializing those
relays costs `Omega(R)`. A fallback that deliberately trades area for simplicity may have `R` much
larger than `E`.

The useful target is therefore:

```text
O(V + I + E + R)
```

where:

- `V` = physical implementation entities;
- `I` = endpoint/net incidences;
- `E` = chosen physical spanning-tree connections;
- `R` = emitted routing relays.

In other words, the fallback should be **linear in its output size**, with no hidden search factor.

If later geometry work proves a stronger bound on `R` for the compiler's bounded-degree physical
circuits, we can state a stronger asymptotic guarantee then. We should not claim strict linear time in
`V + E` before that proof exists.

## Separation from optimized synthesis

The safe fallback should be a distinct policy rather than the last random restart of the normal
optimizer.

Conceptually:

```text
abstract physical circuit
        |
        +--> optimized synthesis
        |       greedy seed
        |       annealing / relaxation
        |       collision-aware routing search
        |
        +--> spacious greedy retries
        |       deterministic greedy seed
        |       lower target fill on failure
        |       wider routing corridors on failure
        |       still uses collision-aware routing search
        |
        +--> deterministic safe fallback
                sparse construction
                fixed routing tracks
                no search
```

The fallback may produce a blueprint several times or orders of magnitude larger than the optimized
one. That is acceptable. Its purpose is to answer:

> Can this physically supported circuit always be materialized into a valid blueprint in predictable
> work, even when quality optimization performs badly?

## Proposed construction

The exact geometry should be proved with tiny Factorio probes before implementation, but the intended
shape is a **channelized routing fabric**.

### 1. Deterministic entity islands

Place implementation entities in stable ID order in widely separated islands. Each island reserves a
large routing yard around the real entity. The yard size may depend on the number of incident physical
port/color groups.

No attempt is made to minimize area.

### 2. Port/color fanout trunks

Physical connections incident to the same `(entity, connector, wire_color)` already belong to one
physical electrical network at that connector. They may therefore share a passive relay trunk without
creating a new unintended connection.

For each such group, construct a deterministic relay chain leaving the entity's routing yard. Every
incident connection receives a distinct tap on that trunk.

This solves the high-degree endpoint problem without requiring arbitrarily many independent first
relays inside one fixed-radius disk around the entity.

### 3. One deterministic routing track per chosen connection

After fanout taps exist, assign each physical connection a private track index. Route its two taps
through a preallocated orthogonal corridor system.

The router must not ask "is this relay position free?" and then search alternatives. Instead, the
track geometry reserves collision-free relay sites by formula from:

```text
(connection_index, segment_index, orientation)
```

A promising construction is a coarse orthogonal grid with horizontal and vertical relay sites placed
on different geometric phases. Circuit wires may cross freely; only relay entities need physical
clearance. The phase separation must guarantee that a horizontal relay and a vertical relay are never
inside each other's collision boxes even when their wires cross.

The hop pitch must be chosen strictly below the configured safe wire span, leaving margin for connector
anchor geometry.

### 4. Deterministic physical-net trees

The present synthesizer chooses a geometry-aware minimum-relay spanning tree after placement. That is a
quality optimization and contains quadratic work for a large net.

The fallback should instead choose a stable linear construction, for example connecting physical-net
endpoints in their already-deterministic endpoint order:

```text
p0 -- p1 -- p2 -- ... -- pn
```

This uses exactly `n - 1` logical connections for an `n`-endpoint net and requires no all-pairs search.
The large routing fabric, rather than the spanning-tree choice, supplies reach safety.

### 5. Validation remains mandatory

Even though correctness should follow from construction, the normal postconditions should still run:

- every implementation entity has one legal position;
- no implementation entities overlap;
- no relay overlaps another entity or a reserved device area;
- every emitted wire segment is within the conservative safe span;
- physical net identities have not accidentally merged;
- blueprint encoding round-trips through the existing codec tests.

The validation pass should be linear or near-linear using spatial bucketing rather than the current
all-pairs relay-clearance checks if fallback layouts become very large.

## Why a channelized fallback is preferable to row placement

Row placement is deterministic and cheap only before routing. It gives no geometric guarantee about
wire distance or relay availability, so the expensive router must recover from a hostile placement.

The safe fallback instead spends area *up front* to make routing trivial:

```text
row fallback:
    minimal placement work
    -> difficult routing problem

channelized fallback:
    deliberately sparse placement
    -> routing positions known by construction
```

The second behavior is what a safety net should provide.

## Interaction with normal synthesis

The fallback should eventually be selectable explicitly and optionally used automatically after a
budget is exceeded.

Possible public policy:

```python
PlacementOptions(strategy="safe")
```

and later:

```python
compile_circuit(
    circuit,
    layout_policy="optimized-with-safe-fallback",
    layout_budget=...,
)
```

The automatic form should use a deterministic budget such as maximum optimization iterations,
routing-search expansions, or elapsed work units rather than relying solely on wall-clock time.
Wall-clock deadlines are useful for a CLI but make compiler output machine-dependent.

The progress API added during the Snake milestone gives us the observability needed to design these
budgets from real circuits.

## Work units and cancellation

Before automatic fallback, synthesis should expose deterministic work counters:

- placement optimization iterations;
- physical connections routed;
- relay candidates tested;
- grid-search expansions;
- relays emitted.

A caller can then request a policy such as:

```text
optimized routing may consume at most N search expansions;
after that, abandon the attempt and rebuild with safe layout.
```

This is preferable to letting one pathological edge consume minutes without a predictable bound.
