# Deterministic safe layout fallback

## Status

The first safe fallback is now implemented as `safe-crossbar`; see `safe-crossbar-layout.md` for the
construction and detailed geometry.

This supersedes the earlier plan to postpone a guaranteed layout until after more heuristic routing
work. Snake demonstrated that row placement and increasingly spacious deterministic greedy placement
could both leave the collision-aware router with no legal late-edge relay chain. The fallback therefore
became a prerequisite for continuing physical-synthesis experiments.

## Design goal

The fallback answers only:

> Can every circuit in the currently supported physical subset be materialized into a deterministic,
> reach-safe blueprint without geometric search?

It deliberately does not optimize compactness, relay count, walking access, or power distribution.
Those belong to the next physical-synthesis milestone.

The priorities are:

1. deterministic behavior;
2. no placement search, routing search, annealing, or random restart;
3. correctness by construction;
4. predictable termination inside the declared supported subset;
5. willingness to consume large amounts of map area and blank relay combinators.

## Implemented construction

The implementation uses one sparse row of real combinators and separates the two Factorio wire colors
into opposite routing half-planes:

```text
RED physical-net buses
======================
      |       |
      |       |
C0    C1      C2      C3      ...
      |               |
      |               |
======================
GREEN physical-net buses
```

Every synthesized RED physical electrical group receives a unique horizontal bus above the entity row.
Every GREEN group receives a unique horizontal bus below it. Each connector/color incidence reaches its
owning bus through a unique vertical feeder column.

The relay lattice uses six-tile hops and fixed coordinate phases. Foreign feeder/bus crossings contain
no relay entity, while the owning crossing receives an explicit tap. As a result, the fallback never
needs to test candidate relay positions or search alternative paths.

The current implementation requires the compiler's default-style wire span to be at least
`sqrt(40) ~= 6.325` tiles; the normal `7.0` span satisfies this.

## Complexity target

Let:

- `V` = real physical entities;
- `I` = physical-net endpoint incidences;
- `R` = emitted relay entities;
- `W` = emitted blueprint wire segments.

The fallback is output-sensitive: construction work is proportional to the generated bus/feeder
structure apart from deterministic sorting. There is no hidden A*, retry, or annealing factor.

A strict `O(V + I)` relay bound is not promised. Farther buses require longer feeders, so `R` can be
superlinear in the original circuit size. This is acceptable for the correctness fallback.

## Supported subset

The first implementation assumes:

- compiler-generated arithmetic, decider, and constant combinators in the existing horizontal target
  orientation;
- blank constant combinators as circuit-wire relays;
- successful synthesis-stage signal allocation and red/green physical-net grouping;
- no fixed user placement anchors;
- a conservative wire span of at least `sqrt(40)` tiles.

Unsupported configuration should fail immediately with a clear contract error, rather than entering a
search/retry process.

## Relationship to optimized synthesis

The physical backend now has a useful conceptual separation:

```text
safe-crossbar
    constructive
    search-free
    potentially huge
    correctness baseline

greedy / net-aware
    topology-aware placement
    collision-aware routing
    smaller when successful
    still heuristic

future optimized net routing
    route physical nets as nets
    share same-net trunks/relays
    optimize area and relay count
    compare against safe-crossbar correctness
```

The next milestone should optimize physical-net routing rather than make safe-crossbar more clever. The
fallback should remain simple enough that its correctness argument stays local and mechanical.

## Work remaining for the fallback itself

The safe path is intentionally minimal. Future maintenance work may improve it without changing its
role:

- widen the public `PlacementStrategy` type to include `safe-crossbar` directly; the current Snake path
  recognizes it before the older optimizer-specific validator;
- support additional target entity orientations or footprints when the compiler emits them;
- add a spatially indexed validation pass if fallback blueprints become large enough that stronger
  geometric postconditions are desirable;
- add explicit anchor support only if it can preserve the constructive guarantee.

None of these is required before beginning optimized net-routing work.
