# Safe folded crossbar physical layout

## Status and rollback contract

`safe-folded-crossbar` is an experimental compactness refinement of the already working linear
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

The interval-packed linear safe crossbar successfully materialized the full Snake circuit without
routing search:

```text
real entities    = 4,912
physical groups  = 4,623
red tracks       = 52
green tracks     = 36
relays           = 330,361
```

However, every real entity still occupied one six-tile-spaced horizontal row. The resulting blueprint
was tens of thousands of tiles wide and not practical to place in game. The problem was physical
extent rather than electrical routability.

The folded strategy addresses only that extent problem. It is not the later optimized-net-routing
milestone.

## Accordion construction

The folded strategy preserves a one-dimensional virtual order of entities and the same interval-track
logic used by the linear construction. It then partitions that order into rows and alternates the row
orientation:

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

A net confined to one row is still one horizontal bus segment. A net whose virtual interval crosses a
fold receives a vertical stitch at the corresponding left or right row boundary.

The number of entities per row is chosen deterministically to balance predicted width and height for
the already-known global red/green track counts. There is no placement optimization or search.

## Why track reuse remains valid

Same-color physical groups are assigned reusable tracks by interval partitioning in virtual linear
coordinates. Two groups share a track only when their feeder intervals are disjoint with relay
clearance.

Folding preserves that property:

- within each row, the map from virtual order to physical x is monotone, either increasing or
  decreasing;
- therefore disjoint virtual intervals remain disjoint physical row segments;
- if a group crosses a row boundary, its virtual interval contains that boundary;
- two disjoint groups on the same track cannot both cross the same boundary.

Thus one track-specific fold portal can be reused by disjoint nets at different boundaries without
shorting them.

## Relay lattice

The folded construction retains the six-tile safe wire-hop lattice and the two-tile bus-track spacing.

Real entity rows are at y coordinates divisible by six. Local bus rows are odd offsets:

```text
RED:   row_y - (3 + 2 * track)
GREEN: row_y + (3 + 2 * track)
```

Endpoint feeder columns remain two tiles left/right of entity centers. Ordinary endpoint-feeder relays
stay on six-tile y positions.

Fold portal columns are placed outside the computation row. Each `(wire color, track)` gets a unique
portal x coordinate. Portal columns are odd x coordinates, while ordinary horizontal bus relays remain
on `x = 0 mod 6`. Ordinary vertical stitch relays remain on `y = 0 mod 6`, while bus rows are odd.
Consequently foreign horizontal/vertical crossings contain no relay entity; only the owning route gets
an explicit tap.

## Compact I/O front panel

The folded entity order deliberately starts with all public input markers followed by all public output
markers, before internal implementation entities. Therefore the external terminals occupy a small
cluster at the beginning of the first row.

This is a layout-only decision. The markers still attach to their ordinary synthesized physical nets.
It fixes the practical Snake problem where movement input and framebuffer output were separated by the
entire one-row implementation.

## Preflight

Before allocating relay entities the folded layout reports:

```text
physical groups
routed groups / singletons
red/green reusable tracks
entity rows
entities per row
exact predicted relay count
predicted width x height
```

The initial safety guards are:

```text
max relays       = 1,000,000
max dimension    = 4,096 tiles
```

A rejected folded preflight does not imply that the circuit is unsynthesizable. The caller may always
switch to the canonical linear `safe-crossbar`, or proceed to a later optimized layout strategy.

## Non-goals

The folded strategy deliberately does not attempt:

- topology-aware entity ordering;
- Steiner or shared-trunk net routing beyond the existing physical-net grouping;
- local track re-coloring after folds;
- area or relay-count optimization beyond deterministic folding;
- substations, walking corridors, or device-aware placement.

Those belong to the next physical-synthesis/net-routing milestone. The folded construction exists only
to make the search-free correctness baseline physically placeable enough for integration testing.
