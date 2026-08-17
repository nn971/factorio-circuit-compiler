# Safe folded crossbar physical layout

## Status and rollback contract

`safe-folded-crossbar` is the search-free, physically bounded refinement of the linear
`safe-crossbar`. The linear construction remains the canonical correctness reference:

```python
safe_crossbar_options()          # strategy = "safe-crossbar"
safe_folded_crossbar_options()   # strategy = "safe-folded-crossbar"
```

The two implementations remain separate. If an in-game probe exposes a folded-layout geometry bug,
callers can immediately switch back to `safe-crossbar` without reverting compiler history.

For Snake the CLI switch is:

```bash
uv run python -m examples.snake_blueprint --linear-safe-layout --output snake-linear.txt
```

## Snake compactness benchmark

The successfully played full Snake circuit is now the physical-layout benchmark. The pre-density-pass
baseline is:

```text
real entities       = 5,668
physical groups     = 5,338
routed groups       = 5,338
red row tracks      = 61
green row tracks    = 38
entity rows         = 13
entities per row    = 436
layout relays       = 470,732
predicted extent    = 3,004 x 2,792 tiles
state period        = 60 ticks
```

Almost all blueprint constant combinators in this benchmark are 1x1 routing relays rather than
implementation constants. The first density pass therefore attacks both dominant pieces of geometric
slack directly:

- relay bus/portal lanes use the true 1x1 relay footprint;
- real implementation rows use the smallest simple center lattice that still preserves failproof
  feeder separation for 2x1 arithmetic/decider/selector combinators.

The first full dense compile with three-tile entities, packed portals, and one-tile bus tracks produced:

```text
real entities       = 5,668
physical groups     = 5,338
red row tracks      = 62
green row tracks    = 38
entity rows         = 13
entities per row    = 437
layout relays       = 247,725
predicted extent    = 1,554 x 1,545 tiles
```

That blueprint exposed an in-game placement-phase bug in the original half-tile bus-row construction.
Restoring the old `first offset = 3, spacing = 2` made the same compact entity/portal layout functional,
which isolated the remaining failure to dense bus geometry rather than the three-tile entity pitch or
packed portal columns.

The benchmark should retain the following measurements after every physical-density change:

- real combinator count;
- physical net/group count;
- RED/GREEN maximum row-local track count;
- entity rows and entities per row;
- layout relay count;
- predicted width and height;
- in-game placeability and functional Snake behavior.

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
to balance width and height. This is formula-driven sizing rather than placement search. Because both
entity pitch and row pitch enter this calculation, density improvements can change the selected
row/column shape rather than merely shrinking the old rectangle in place.

## Why row-local tracks are required

The first folded implementation incorrectly reused the linear crossbar's global interval-track
assignment. Disjoint virtual intervals do not necessarily remain disjoint after folding because a
cross-row route's physical row segment extends from its endpoint to an incoming or outgoing fold
portal.

Full Snake exposed the counterexample directly:

```text
safe-folded-crossbar formula assigned one relay site to distinct groups:
(2262.0, 179.0) -> 355, 356
```

The corrected planner therefore works from actual physical row segments:

1. choose deterministic folded entity rows;
2. determine which physical nets cross each fold boundary;
3. assign every crossing net a boundary-local portal column;
4. for every `(physical net, entity row)`, collect endpoint feeder taps plus incoming/outgoing portals;
5. form the exact closed physical x interval between those attachments;
6. for each `(row, wire color)` independently, interval-color those physical intervals;
7. place RED row tracks above the entity row and GREEN row tracks below it;
8. connect adjacent rows of the same physical net through deterministic portal stitches.

A net may use different local track numbers on adjacent rows. For each row/color, interval partitioning
uses the minimum number of tracks for the already-fixed segment intervals. Segments sharing a physical
track remain separated by the relay-center clearance.

## Density pass 1: packed relay lanes

### Bus tracks

The old folded baseline used:

```text
first bus offset:   3 tiles
bus track spacing:  2 tiles
```

That leaves an unused tile between adjacent 1x1 relay rows. The dense target is:

```text
wire relay hop pitch:  6 tiles
first bus offset:      3 tiles
bus track spacing:     1 tile
```

The first attempted dense construction instead used `first offset = 3.5`, placing horizontal bus/tap
relays on half-integer y coordinates while fold-stitch and feeder relays remained on integer y
coordinates. Small no-fold probes showed that half-tile rows and one-tile row spacing are individually
legal, but folded probes exposed missing intermediate wire segments. Runtime inspection showed the
surviving endpoints were within wire reach; the failure arose when intended integer and half-integer
1x1 relay sites were mapped to Factorio's placement grid.

The corrected policy keeps **every routing relay on one integer blueprint-coordinate phase**:

```text
RED:   row_y - (3 + local_track)
GREEN: row_y + (3 + local_track)
```

Adjacent bus tracks remain one tile apart. Regular endpoint feeders and fold stitches still use the
six-tile hop lattice. If a bus tap itself lies on `y = 0 (mod 6)`, the stitch constructor reuses that tap
and adds regular stitch relays only strictly between its upper and lower fold taps. Preflight relay
counting mirrors the same strictly-between rule.

### Portal columns

Fold portals are also 1x1 constants. The old policy spent two columns per portal to keep every portal
on an odd x coordinate. The dense policy packs portal centers onto adjacent integer columns and skips
only `x = 0 (mod 6)` columns used by ordinary horizontal row-bus relays.

For a fold whose computation-row edge is `x = 0 (mod 6)`, portal offsets begin:

```text
9, 10, 11, 13, 14, 15, 16, 17, 19, ...
```

Thus portal lanes have average pitch close to one tile while preserving the deterministic crossing
invariant.

## Density pass 1: three-tile real-entity rows

The old policy also spent six horizontal tiles on every implementation entity. The current Factorio
target only needs two implementation footprint cases:

```text
constant combinator                 1 x 1
arithmetic / decider / selector     2 x 1
```

A general `EntityGeometry` abstraction is unnecessary for this target. The failproof row can use a
uniform **three-tile center pitch**, which is already safe for the larger 2x1 case.

Entity centers therefore alternate between:

```text
x = 0 (mod 6)
x = 3 (mod 6)
```

With the existing two-tile feeder offset, endpoint feeder columns occupy only:

```text
INPUT / SINGLE: center - 2  -> residues 1 or 4 (mod 6)
OUTPUT:         center + 2  -> residues 2 or 5 (mod 6)
```

They never occupy `x = 0 (mod 6)`, which remains reserved for ordinary horizontal row-bus relays.
Input/output feeder columns from neighbouring entities are also distinct; their minimum center spacing
is one tile, which is legal for 1x1 relay constants.

Two 2x1 implementation combinators whose centers are three tiles apart retain one full tile of empty
space between their footprints.

For multi-row layouts, the planner only considers an odd number of entity columns. Then the final
entity center of every row is again `x = 0 (mod 6)`, so the existing compact portal-offset sequence is
valid on both fold sides. A single-row circuit may use an even column count because it has no fold
portals.

## Combined crossing and placement-phase invariant

The dense constructive geometry separates relay families structurally while keeping all routing relay
coordinates integral:

```text
real entity centers:          x = 0 or 3 (mod 6), y = 0 (mod row_pitch)
INPUT/SINGLE feeder columns:  x = 1 or 4 (mod 6)
OUTPUT feeder columns:        x = 2 or 5 (mod 6)
regular vertical relays:      integer x/y; six-tile y hops
horizontal bus rows:          integer y; adjacent tracks one tile apart
regular horizontal relays:    x = 0 (mod 6)
portal columns:                integer x, excluding x = 0 (mod 6)
```

Because feeders and portals are excluded from the ordinary row-bus x lattice, integer bus rows may
cross the six-tile vertical y lattice without sharing relay centers between unrelated route families.
Keeping all 1x1 routing relays on one coordinate phase also prevents Factorio placement from collapsing
an intended integer/half-integer pair onto one tile.

The existing row-local interval-coloring invariant still prevents unrelated horizontal segments
assigned to one bus track from overlapping.

## Cheap in-game probe

Before recompiling full Snake, generate the final integer-lattice fold probes:

```bash
uv run python -m factorio_circuit.probes.integer_dense_fold_geometry \
  --output-dir probe-blueprints
```

This writes:

```text
integer-dense-fold-red.txt
integer-dense-fold-red-green.txt
```

Both use the production `3 / 1` bus geometry, packed portal residues, odd-column fold edge, endpoint
feeders, and vertical stitches. Each labelled sink must see exactly the unique `signal-A` count written
in its description. They are intended as the cheap manual acceptance gate before another full Snake
compile.

## Compact I/O front panel

The folded entity order starts with public input markers followed by public output markers, before
internal implementation entities. External terminals therefore occupy a small cluster at the beginning
of the first row. This remains a layout-only decision; markers attach to ordinary synthesized physical
nets.

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

Safety guards remain:

```text
max relays       = 1,000,000
max dimension    = 4,096 tiles
```

Relay counting uses the same finalized row-local track and portal plan as construction, and the builder
asserts that emitted relay count matches preflight exactly.

## Regression invariants

Tests cover the properties on which the constructive proof depends:

- actual portal-extended segments sharing a `(row, color, track)` remain disjoint by the configured
  relay-center clearance;
- adjacent bus-track relay centers may be exactly one tile apart;
- all bus rows and all emitted routing relays stay on the integer blueprint-coordinate lattice;
- fold-stitch preflight counts exclude upper/lower fold taps even when a tap lies on `y = 0 (mod 6)`;
- packed portal columns are at least one tile apart and never occupy `x = 0 (mod 6)`;
- real entity rows use three-tile centers, and +/-2 feeder columns stay off `x = 0 (mod 6)`;
- every multi-row folded plan uses an odd column count, keeping both row edges on the six-tile lattice;
- folded layout remains deterministic and performs no heuristic routing search;
- linear and folded safe strategies remain independent rollback paths.

## Next compactness targets

Once the `3 / 1` integer-lattice probes and then full Snake are confirmed in game, the next useful
targets are more structural:

1. record the new Snake relay count and extent against both the 3,004 x 2,792 / 470,732 baseline and
   the failed half-tile dense compile's 1,554 x 1,545 / 247,725 result;
2. improve deterministic entity ordering using physical-net topology to shorten row segments and reduce
   fold crossings;
3. reduce relay count by sharing trunks and introducing stronger constructive routing structures;
4. only then consider more aggressive mixed-width packing of the comparatively few implementation
   constants if the benchmark shows a measurable benefit.

The current density pass stays within the failproof constructive-layout philosophy: every coordinate is
still formula-derived, with no placement search, routing search, retry, or backtracking.
