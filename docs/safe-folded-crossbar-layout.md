# Safe folded crossbar physical layout

## Status and rollback contract

`safe-folded-crossbar` is the search-free, physically bounded refinement of the linear
`safe-crossbar`. The linear construction remains the canonical correctness reference and rollback
path:

```python
safe_crossbar_options()  # strategy = "safe-crossbar"
safe_folded_crossbar_options()  # strategy = "safe-folded-crossbar"
```

For the heavyweight Snake benchmark the linear rollback is:

```bash
uv run python -m benchmarks.snake.generate \
  --linear-safe-layout \
  --output snake-linear.txt
```

The dense folded geometry described below has been validated by a full in-game Snake playtest.
Canonical historical benchmark measurements now live in
`benchmarks/snake/baselines.json`; this document records the algorithm and the layout lessons that
produced those measurements.

## Snake density milestone

The accepted pre-density Snake layout used 470,732 routing relays and a 3,004 x 2,792 tile extent. The
validated dense layout uses 246,476 relays and a 1,554 x 1,544 tile extent, while preserving the same
5,657 implementation combinators and 60-tick state period.

That is approximately:

- **47.6% fewer routing relays**;
- **48.3% less width**;
- **44.7% less height**;
- **71.4% less bounding-box area**.

The full before/after row, column, track, group, extent, commit, and validation records are kept in
`benchmarks/snake/baselines.json` rather than duplicated as the mutable source of truth here.

Almost all constant combinators in the large Snake blueprint are 1x1 routing relays rather than
implementation constants, so layout density is dominated by relay lanes and routed-segment length.

## Accordion entity geometry

The folded strategy keeps a deterministic one-dimensional entity order and partitions it into
serpentine rows:

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

A same-row net uses one horizontal segment. A net crossing a row boundary receives a boundary-local
portal and a vertical fold stitch. Public input/output markers are ordered first, so the first row forms
a compact external I/O panel.

## Row-local track assignment

Folding changes interval geometry: two nets whose virtual intervals are disjoint can overlap physically
once their row segments are extended to fold portals. Therefore the folded planner does not reuse the
linear crossbar's global track assignment.

After entity rows and portals are fixed it:

1. collects endpoint feeder taps and incoming/outgoing portal attachments for every `(net, row)`;
2. forms the exact closed horizontal interval for that physical row segment;
3. interval-colors segments independently for each `(row, wire color)`;
4. places RED tracks above the entity row and GREEN tracks below it;
5. connects adjacent row segments of a cross-row net through its deterministic portal stitch.

A physical net may use different local track numbers on adjacent rows. Segments sharing one local track
remain separated by the relay-center clearance.

This row-local rule was introduced after full Snake exposed a real collision in the first folded draft,
which had reused global linear track identities after portal extension.

## Density pass: three-tile implementation pitch

The current physical target needs only these implementation footprints:

```text
constant combinator                 1 x 1
arithmetic / decider / selector     2 x 1
```

The folded layout therefore uses a uniform **3-tile entity-center pitch** rather than the old 6-tile
pitch. Entity centers alternate between:

```text
x = 0 (mod 6)
x = 3 (mod 6)
```

With the existing two-tile feeder offset:

```text
INPUT / SINGLE feeder: center - 2 -> x = 1 or 4 (mod 6)
OUTPUT feeder:         center + 2 -> x = 2 or 5 (mod 6)
```

Feeder columns therefore never occupy the ordinary horizontal relay lattice `x = 0 (mod 6)`. Two 2x1
implementation combinators whose centers are three tiles apart still leave one full empty tile between
their footprints.

For multi-row layouts the planner considers only odd column counts. The right edge then returns to
`x = 0 (mod 6)`, keeping the packed portal construction phase-stable on every fold. Single-row layouts
may use an even column count because they have no portals.

## Density pass: packed portal columns

Fold portals are 1x1 relays. Rather than spending two columns per portal, the dense policy packs them on
adjacent integer columns while skipping the `x = 0 (mod 6)` row-bus lattice.

For a fold edge on `x = 0 (mod 6)`, portal offsets begin:

```text
9, 10, 11, 13, 14, 15, 16, 17, 19, ...
```

This gives close to one-tile average portal pitch without creating relay-center conflicts with ordinary
horizontal row-bus relays.

## Density pass: one-tile bus tracks on one coordinate phase

The old folded layout used:

```text
first bus offset:   3 tiles
bus track spacing:  2 tiles
```

The validated dense policy is:

```text
relay hop pitch:    6 tiles
first bus offset:   3 tiles
bus track spacing:  1 tile
```

so:

```text
RED:   row_y - (3 + local_track)
GREEN: row_y + (3 + local_track)
```

Every routing relay is emitted at integral blueprint coordinates. This single-coordinate-phase rule is
important. An earlier experiment used half-integer horizontal bus rows while feeder/fold stitches
remained integral. Small no-fold routes worked, but realistic folded probes lost selected intermediate
wire connections after Factorio placement. Runtime inspection showed the surviving endpoints were still
within wire reach. Keeping every 1x1 routing relay on the same integer placement phase removed the
failure, and the final full Snake then ran correctly in game.

A fold tap can itself lie on the six-tile vertical stitch lattice. Construction reuses that tap and adds
only regular stitch relays strictly between the upper and lower taps.

## Combined constructive invariant

The dense layout separates relay families structurally:

```text
real entity centers:          x = 0 or 3 (mod 6), row-aligned y
INPUT/SINGLE feeder columns:  x = 1 or 4 (mod 6)
OUTPUT feeder columns:        x = 2 or 5 (mod 6)
all routing relays:           integer x/y coordinates
regular vertical relays:      six-tile y hops
horizontal bus tracks:        adjacent integer y rows
regular horizontal relays:    x = 0 (mod 6)
portal columns:               integer x, excluding x = 0 (mod 6)
```

Row-local interval coloring prevents unrelated horizontal segments on the same track from overlapping.
The x-residue rules keep vertical feeder/portal families off the ordinary horizontal relay centers.

## Portal-aware row sizing

Row width strongly affects fold cost. A narrower row can look attractive geometrically while causing
many more nets to cross row boundaries, creating large portal margins and long stitch structures.

The planner therefore does not estimate portal capacity from global track count. Before choosing a
column count it computes the number of routed physical nets crossing **every virtual cut** in the
ordered entity list. For each candidate row width, it evaluates the cuts that would become fold
boundaries and uses their maximum crossing count to determine portal margin.

This remains deterministic and search-free, but avoids pathological choices such as the transient
18-row / 315-column Snake layout that appeared when portal cost was only approximated.

## Exact preflight relay accounting

Integer-lattice packing creates legitimate same-net coordinate reuse: an endpoint tap, row-bus relay,
portal tap, or fold-stitch role can sometimes refer to the same physical relay site.

Preflight therefore counts **unique `(x, y)` relay sites per physical net**, mirroring construction's
`add_relay()` deduplication. The builder retains a strict assertion that predicted and emitted relay
counts are identical.

Preflight reports:

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

## Regression invariants

Routine tests cover the durable constructive properties rather than compiling the full Snake workload:

- folded synthesis remains deterministic and never invokes heuristic routing search;
- actual portal-extended row segments sharing a `(row, color, track)` remain separated;
- implementation rows use 3-tile entity-center pitch;
- feeder residues remain off `x = 0 (mod 6)`;
- multi-row plans use odd column counts;
- packed portal columns stay at least one tile apart and skip `x = 0 (mod 6)`;
- adjacent bus tracks are one tile apart;
- every emitted routing relay stays on the integer blueprint-coordinate lattice;
- fold-stitch counting excludes fold taps already occupying stitch-lattice sites;
- row sizing uses actual route-cut crossing counts;
- predicted unique relay count matches emitted relay count;
- the linear and folded safe strategies remain independent rollback paths.

The full Snake compile and in-game playtest are an explicit heavyweight acceptance benchmark; see
`benchmarks/snake/README.md`.

## Next compactness targets

The first density pass has largely exhausted simple lane-spacing gains without changing routing
topology. The next useful targets are structural:

1. improve deterministic entity ordering using net topology to shorten row segments and reduce fold
   crossings;
2. reduce relay count through stronger constructive trunk sharing or hierarchical routing;
3. reassess mixed-width implementation packing only if later benchmarks show implementation footprint,
   rather than relay topology, becoming significant.

The strategy remains formula-derived and failproof by construction: no placement search, routing
search, retry, or backtracking is introduced.
