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

`safe-crossbar` is now the canonical search-free reference implementation.

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

`safe-folded-crossbar` is implemented separately. It preserves the virtual one-dimensional entity order
and interval-track assignment of the linear reference, then folds that order into deterministic
serpentine rows.

A net confined to one row remains a horizontal bus segment. A net whose virtual interval crosses a fold
receives a vertical stitch on a track-specific portal column outside the computation row. Public input
and output markers are placed together at the start of the first row.

The folded implementation is deliberately **not** allowed to replace the linear fallback. It is a
separate strategy so an in-game defect can be bypassed immediately:

```text
safe-crossbar          canonical one-row reference
safe-folded-crossbar   bounded-footprint refinement
```

Folded preflight computes the exact relay count and predicted width/height before relay allocation. The
initial guards are one million relays and a 4096-tile maximum dimension.

## Why the folded construction is still search-free

The folding map is monotone inside each row, alternating direction on adjacent rows. Therefore two
same-color groups that have disjoint virtual intervals remain disjoint within every folded row.

At a row boundary, only a group whose virtual interval crosses that boundary receives a vertical stitch.
Two disjoint groups sharing a track cannot both cross the same boundary.

Relay lattice phases keep foreign crossings empty:

- ordinary horizontal bus relays are at `x = 0 mod 6`;
- endpoint feeder columns are at `x = +/-2 mod 6` relative to entity centers;
- fold portal columns are odd x coordinates outside the computation row;
- ordinary vertical feeder/stitch relays are at `y = 0 mod 6`;
- bus rows are odd y offsets from entity rows.

Only owning intersections receive explicit tap relays.

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
