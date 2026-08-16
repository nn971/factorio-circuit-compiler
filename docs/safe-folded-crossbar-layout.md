# Safe folded crossbar physical layout

## Status and rollback contract

`safe-folded-crossbar` is an experimental placeability refinement of the already working linear
`safe-crossbar`. It is intentionally implemented in a separate module and selected by a separate
strategy name.

The linear construction remains the canonical correctness reference:

```python
safe_crossbar_options()          # strategy = "safe-crossbar"
safe_folded_crossbar_options()   # strategy = "safe-folded-crossbar"
```

The folded implementation must never replace or silently alter the linear strategy. If an in-game
probe exposes a folded-layout bug, callers can immediately switch back to `safe-crossbar` without
reverting compiler history.

For Snake the CLI switch is:

```bash
uv run python -m examples.snake_blueprint --linear-safe-layout --output snake-linear.txt
```

## Motivation

The interval-packed linear safe crossbar successfully materialized full Snake without routing search:

```text
real entities    = 4,912
physical groups  = 4,623
red tracks       = 52
green tracks     = 36
relays           = 330,361
```

However, every real entity still occupied one six-tile-spaced horizontal row. The resulting blueprint
was tens of thousands of tiles wide and not practical to place in game. The folded strategy addresses
that extent problem only; it is not the later optimized-net-routing milestone.

## Accordion entity geometry

The folded strategy preserves a deterministic one-dimensional entity order, then partitions it into
rows and alternates row orientation:

```text
virtual order:

0 -> 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> ...

folded geometry:

0 -> 1 -> 2 -> 3
               |
7 <- 6 <- 5 <- 4
|
8 -> 9 -> ...
```

A net confined to one row uses one horizontal row segment. A net crossing a fold receives a vertical
stitch at that row boundary. Public input and output markers appear first in the entity order, so the
first row acts as a compact external I/O front panel.

The number of entities per row is chosen deterministically from a conservative linear-overlap estimate
to balance width and height. This is formula-driven sizing rather than placement search.

## Important correction: global linear tracks do not survive folding

The first folded implementation incorrectly reused the linear crossbar's global interval-track
assignment. The proof claimed that disjoint virtual intervals would remain disjoint after folding.
That is not sufficient because a cross-row route's *physical row segment* is extended from its endpoint
to an incoming or outgoing fold portal. The portal extension is not part of the original virtual
endpoint interval.

Full Snake exposed the counterexample directly:

```text
safe-folded-crossbar formula assigned one relay site to distinct groups:
(2262.0, 179.0) -> 355, 356
```

Two groups that were permitted to reuse one linear track acquired overlapping horizontal row segments
after portal extension.

The corrected folded strategy therefore does **not** inherit global bus-track identities from
`safe-crossbar`.

## Correct row-local construction

The corrected planner proceeds in this order:

1. choose the deterministic folded entity rows;
2. determine which physical nets cross each fold boundary;
3. assign every crossing net a boundary-local portal column;
4. for every `(physical net, entity row)`, collect all actual horizontal attachments:
   - endpoint feeder taps on that row;
   - incoming fold portal, if present;
   - outgoing fold portal, if present;
5. form the exact closed physical x interval between those attachments;
6. for each `(row, wire color)` independently, interval-color those **actual physical row segments**;
7. place RED row tracks above the entity row and GREEN row tracks below it;
8. connect adjacent rows of the same physical net through the deterministic portal stitch.

A net is allowed to use different local track numbers on adjacent rows. The fold stitch connects the
corresponding two bus heights, so no global track identity is required.

For each row/color, interval partitioning uses the minimum number of tracks for the already-fixed
segment intervals. Therefore two segments sharing one physical bus row are guaranteed disjoint with
the relay-center clearance applied.

## Portal columns

Portal columns are assigned independently at every row boundary. All physical nets crossing one
boundary receive distinct portal ordinals, across both wire colors. The same ordinal may be reused at a
different boundary because the vertical stitch bands are vertically disjoint.

The fold side alternates with the serpentine rows. Portal columns remain outside the computation row and
on odd x coordinates. This keeps them disjoint from ordinary horizontal bus-relay sites at
`x = 0 mod 6`.

## Relay lattice

The folded construction retains:

```text
wire relay pitch:   6 tiles
bus track spacing:  2 tiles
first bus offset:   3 tiles
```

Real entity rows are at y coordinates divisible by six. Local bus rows are odd offsets:

```text
RED:   row_y - (3 + 2 * local_track)
GREEN: row_y + (3 + 2 * local_track)
```

Endpoint feeder columns are two tiles left/right of entity centers, while ordinary endpoint-feeder
relays stay on six-tile y positions. Fold stitches use odd portal x coordinates and ordinary relays at
six-tile y positions. Foreign horizontal/vertical wire crossings therefore contain no relay entity;
only owning routes receive explicit taps.

Row pitch is computed from the maximum RED and GREEN **row-local** track counts, plus a fixed margin, so
relay bands belonging to adjacent entity rows remain separated.

## Compact I/O front panel

The folded entity order deliberately starts with all public input markers followed by all public output
markers, before internal implementation entities. The external terminals therefore occupy a small
cluster at the beginning of the first row.

This is a layout-only decision. The markers still attach to their ordinary synthesized physical nets.
It fixes the practical Snake problem where movement input and framebuffer output were separated by the
entire linear implementation.

## Preflight

Before allocating relay entities the folded layout reports:

```text
physical groups
routed groups / singletons
maximum row-local red/green tracks
entity rows
entities per row
exact predicted relay count
predicted width x height
```

The initial safety guards remain:

```text
max relays       = 1,000,000
max dimension    = 4,096 tiles
```

Relay counting uses the same finalized row-local track and portal plan as construction, and the builder
asserts that the emitted relay count matches preflight exactly.

A rejected folded preflight does not imply that the circuit is unsynthesizable. The caller may always
switch to the canonical linear `safe-crossbar`, or proceed to a later optimized layout strategy.

## Regression invariant

Tests now inspect the planner directly. For every `(row, color, local track)`, the actual portal-extended
physical intervals assigned to that track must remain disjoint by at least the relay-center clearance.
This is the invariant violated by the original `(2262, 179)` Snake failure.

## Non-goals

The folded strategy deliberately does not attempt:

- topology-aware entity ordering;
- Steiner or shared-trunk net routing beyond existing physical-net grouping;
- optimized portal assignment;
- area or relay-count optimization beyond deterministic folding and interval packing;
- substations, walking corridors, or device-aware placement.

Those belong to the next physical-synthesis/net-routing milestone. The folded construction exists only
to make the search-free correctness baseline physically placeable enough for integration testing.
