# Layout safe fallback plan

## Goal

Physical synthesis needs a deterministic correctness baseline that can materialize supported abstract
physical circuits without depending on heuristic placement/routing success.

The key requirement is not compactness. It is a construction whose failure modes are explicit and whose
runtime is output-sensitive in the geometry it emits.

For a realized layout with

- `V` real physical entities,
- `I` endpoint/net incidences,
- `E` chosen real-endpoint electrical connections, and
- `R` emitted relay entities,

a strict `O(V + E)` target is impossible when bounded wire reach forces `R` relays. The relevant target
is therefore output-sensitive work such as `O(V + I + E + R)` plus small deterministic sorting terms.

## Implemented canonical fallback: linear safe crossbar

`safe-crossbar` is the canonical search-free reference implementation.

It places real entities on one sparse horizontal row, builds one bus segment per physical electrical
group, reuses same-color tracks for disjoint linear intervals, and connects endpoints through fixed
vertical feeder columns. Relay hops use a six-tile lattice under the normal seven-tile conservative wire
span. No A*, lane search, annealing, or retry loop is used.

The initial one-net-per-row version produced more than twenty million relays for full Snake. The current
interval-packed version reduced the observed Snake construction to roughly 330k relays with 52 red and
36 green reusable tracks, but the one-row module was still tens of thousands of tiles wide.

This linear strategy remains intentionally available as the canonical rollback/reference path even as
more practical safe layouts are added.

## Optional placeability refinement: folded safe crossbar

`safe-folded-crossbar` is implemented separately. It preserves deterministic entity order, folds that
order into serpentine rows, and places public input/output markers together at the start of the first
row.

The folded implementation is deliberately **not** allowed to replace the linear fallback. It is a
separate strategy so an in-game defect can be bypassed immediately:

```text
safe-crossbar          canonical one-row reference
safe-folded-crossbar   bounded-footprint refinement
```

Folded preflight computes the exact relay count and predicted width/height before relay allocation. The
initial guards are one million relays and a 4096-tile maximum dimension.

## Corrected folded routing model

The first folded draft reused the linear crossbar's global interval-track assignment. Full Snake exposed
why that proof was insufficient: a cross-row net extends its horizontal physical segment to a fold
portal, and that portal extension is not part of the original virtual endpoint interval. Two virtual
intervals that safely shared a linear track could therefore acquire overlapping physical row segments.

The observed failure was:

```text
safe-folded-crossbar formula assigned one relay site to distinct groups:
(2262.0, 179.0) -> 355, 356
```

The corrected folded construction is row-local:

1. choose deterministic entity rows;
2. determine which physical nets cross each fold boundary;
3. assign crossing nets boundary-local portal columns;
4. form the exact horizontal attachment interval for every `(physical net, row)`, including endpoint
   taps and incoming/outgoing portals;
5. interval-color those **actual physical row segments** independently for each `(row, wire color)`;
6. place RED local buses above the row and GREEN local buses below it;
7. connect adjacent rows of one net with a vertical stitch between that net's two local bus heights.

A net may therefore use different local track numbers on adjacent rows. No global folded track identity
is assumed.

## Why the corrected folded construction is search-free

All geometric choices are still formula-generated:

- entity row/column positions are deterministic;
- portal ordinals are deterministic within each fold boundary;
- row-segment track assignment is deterministic interval partitioning;
- relay sites follow fixed six-tile wire lattices and two-tile bus-track spacing.

For every `(row, color, local track)`, segments assigned to the same bus row are disjoint by the relay
clearance rule *after portal extensions are included*. Tests inspect this invariant directly.

Relay lattice phases keep foreign crossings empty:

- ordinary horizontal bus relays are at `x = 0 mod 6`;
- endpoint feeder columns are at `x = +/-2 mod 6` relative to entity centers;
- fold portal columns are odd x coordinates outside the computation row;
- ordinary vertical feeder/stitch relays are at `y = 0 mod 6`;
- bus rows are odd y offsets from entity rows.

Only owning intersections receive explicit tap relays. Row pitch is computed from the maximum row-local
RED/GREEN track counts plus a deterministic margin, keeping adjacent row routing bands separated.

## Still deferred: optimized net routing

Neither safe strategy is intended to solve the next optimization milestone. Future physical synthesis
may improve:

- topology-aware entity ordering;
- shared trunks and Steiner-like routing for physical nets;
- local versus global channel allocation;
- relay and area minimization;
- placement of substations/walking corridors;
- device-aware placement and module composition;
- parallel execution of independent search attempts.

Optimized strategies should be tested against `safe-crossbar` for electrical equivalence and may use
`safe-folded-crossbar` as a practical integration baseline.
