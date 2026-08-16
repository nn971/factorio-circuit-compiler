# Safe crossbar physical layout

## Purpose

`safe-crossbar` is the compiler's canonical constructive physical-layout fallback. It separates two
questions that were previously entangled:

1. can a supported physical circuit be materialized without routing search?;
2. can that blueprint be compact and relay-efficient?

Safe-crossbar answers only the first question. It deliberately spends map area and blank constant
combinators so placement and routing never need A*, collision backtracking, annealing, or retries.

The first implementation assigned one globally unique bus row to every physical electrical group.
Snake exposed why that is too pessimistic: 4,623 physical groups produced more than twenty million
relays because progressively farther bus rows made endpoint feeders grow roughly quadratically. The
current implementation keeps the constructive guarantee but **reuses bus tracks for disjoint net
intervals**, packs those tracks two tiles apart, and puts feeder-heavy tracks closest to the entity row.

This one-row construction is intentionally retained unchanged as the **reference/rollback strategy**.
The experimental `safe-folded-crossbar` lives in a separate module and mechanically folds this linear
ordering into serpentine rows. If folded placement exposes an in-game problem, callers can switch back
to `safe-crossbar` without reverting compiler history.

## Construction

All real implementation entities are placed on one sparse horizontal row at six-tile intervals:

```text
C0      C1      C2      C3      ...
```

Physical electrical groups are built directly from synthesis-stage `net_groups` rather than first
being flattened into geometry-selected spanning-tree edges.

- RED groups use horizontal bus segments above the entity row;
- GREEN groups use horizontal bus segments below the entity row;
- every concrete connector/color incidence receives one vertical feeder from the entity to its bus.

INPUT and SINGLE connectors use the feeder column two tiles left of the entity centre. OUTPUT
connectors use the column two tiles right of the entity centre. Because entity centres are spaced by
six tiles, feeder columns are globally distinct for a given connector/color incidence.

Single-endpoint physical groups require no wire and therefore consume no bus track or relay.

## Reusable interval tracks

For every multi-endpoint physical group, safe-crossbar computes the closed horizontal interval between
its leftmost and rightmost feeder columns. For each color independently, those intervals are assigned
to reusable bus tracks.

Two groups may share one track when the previous group's right edge plus relay-center clearance is at
or before the next group's left edge. The current clearance is 1.1 tiles, matching the normal router's
1x1 relay collision margin.

```text
track 0:   ===== net A =====          === net D ===
track 1:        ========= net B =========
track 2:                ===== net C =====

entity row:
C0      C1      C2      C3      C4      C5
```

Track assignment is the standard deterministic interval-partitioning algorithm:

1. sort intervals by left endpoint, then right endpoint, then physical group id;
2. release every track whose previous interval has ended plus clearance;
3. reuse the lowest-numbered available track;
4. otherwise allocate the next track.

A heap implementation runs in `O(G log G)` for `G` routed physical groups and uses the minimum possible
number of tracks for the fixed entity order and clearance rule.

The temporary track identities are then reordered as whole tracks by total endpoint count. The track
carrying the most endpoint feeders becomes track zero, the next-heaviest becomes track one, and so on.
This preserves every non-overlap proof while minimizing weighted feeder depth for that fixed interval
partition.

The important size parameter is therefore no longer the total number of physical nets. It is the
**maximum same-color interval overlap**, i.e. the routing cutwidth induced by the current entity order.

## Decoupled routing pitch and track spacing

The default compiler wire span is conservatively seven tiles. Relays along a wire use a six-tile hop
pitch, but bus tracks do **not** need to be six tiles apart.

Safe-crossbar uses:

```text
wire relay pitch:   6 tiles
bus track spacing:  2 tiles
first bus offset:   3 tiles
```

Thus RED bus rows are `y = -3, -5, -7, ...` and GREEN rows are `y = +3, +5, +7, ...`.
All bus rows are odd integer coordinates, while ordinary vertical feeder relays remain at
`y = +/-6, +/-12, ...`. The two relay lattices therefore never occupy the same point.

The longest local entity-to-first-feeder hop remains

```text
sqrt(2^2 + 6^2) = sqrt(40) ~= 6.325 tiles
```

so the implementation requires

```text
blueprint_safe_wire_span >= sqrt(40)
```

and works with the default value `7.0`.

The x-coordinate phases are:

```text
real entity centres:       x = 0 mod 6, y = 0
INPUT/SINGLE feeders:      x = -2 mod 6
OUTPUT feeders:            x = +2 mod 6
ordinary bus relays:       x = 0 mod 6
owning taps:               feeder x on owning bus y
```

## Why unrelated nets do not short

Consider a vertical feeder crossing a foreign horizontal bus of the same color. The feeder is at
`x = +/-2 mod 6`, while ordinary bus relays exist only at `x = 0 mod 6`. Therefore the foreign crossing
has no bus relay entity. Ordinary feeder relays occur only at multiples of six in y, while bus rows are
odd, so they do not create a relay at that crossing either. The wire segments may geometrically cross,
but the electrical networks remain disconnected.

The feeder's owning bus receives an explicit tap relay at the crossing. Only that crossing joins the
feeder to a bus.

Different feeders are globally distinct columns. Different simultaneously overlapping buses use
distinct track rows two tiles apart. Groups that reuse one track have disjoint relay intervals with at
least the configured center clearance. RED and GREEN relay systems live in opposite half-planes.

The implementation asserts that no formula-generated relay coordinate is assigned to two distinct
physical groups.

## Exact preflight and safety cap

Because track assignment happens before relay allocation, the relay count is known exactly before any
`LayoutRelay` objects are created.

For each route, the estimator counts:

- one tap for every endpoint;
- ordinary vertical feeder relays at six-tile steps before the bus row;
- ordinary horizontal bus relays every six tiles inside that group's own interval.

Safe-crossbar reports a preflight summary such as:

```text
safe-layout: preflight: groups=4623; routed=...; singletons=...;
             tracks=red:...,green:...; predicted_relays=...
```

The default safety cap is **1,000,000 generated relays**. If the exact prediction exceeds that value,
synthesis refuses before allocating the huge relay graph or attempting blueprint JSON/zlib encoding.
The low-level builder accepts `max_relays=None` only for deliberate experiments that explicitly want an
unbounded fallback layout.

After construction, the implementation asserts that the emitted relay count equals the preflight
prediction.

## Routing algorithm

For every multi-endpoint physical group:

1. compute its endpoint feeder interval;
2. assign the interval to the minimum deterministic reusable track;
3. reorder whole tracks by endpoint weight;
4. construct one tap for every endpoint;
5. connect the real endpoint through six-tile feeder hops to the tap;
6. emit ordinary bus relays every six tiles inside that group's own interval;
7. sort that group's bus relays and taps by x and connect adjacent nodes.

The normal `route_wires()` implementation, parallel-lane search, grid fallback, placement annealing, and
retry loop are not called.

For physical simulation, `PhysicalCircuit.connections` still contains a relay-free deterministic chain
between the real endpoints of each physical group. `Layout.wires` contains the actual reach-safe relay
geometry used by blueprint serialization.

## Complexity

Let

- `V` be real physical entities;
- `G` be routed physical groups;
- `R` be generated relay entities;
- `W` be emitted blueprint wire segments.

Track assignment is `O(G log G)`. Construction is output-sensitive in `R + W`; there is no search or
backtracking factor.

The fallback can still be large when the fixed entity order has high interval overlap or very long net
spans. That is intentional: safe-crossbar guarantees a predictable construction, while the next
physical-synthesis milestone is responsible for improving entity order, physical-net routing, area, and
relay count.

## Supported subset and deliberate limitations

The current implementation assumes:

- compiler-generated arithmetic, decider, and constant combinators with the existing horizontal target
  orientation;
- blank constant combinators as wire relays;
- the compiler's synthesized red/green physical-net grouping is valid;
- no fixed user placement anchors;
- a configured safe wire span of at least `sqrt(40)` tiles.

It does not attempt walking corridors, substation placement, device-aware placement, or optimized
physical-net routing.

Safe-crossbar should remain the correctness baseline against which the folded fallback and later
optimizers are tested.

## Snake reference command

Snake now defaults to `safe-folded-crossbar` for placeability. To deliberately use this canonical
one-row reference instead:

```bash
uv run python -m examples.snake_blueprint \
  --linear-safe-layout \
  --output snake-linear.txt
```

Progress and synthesis diagnostics remain on stderr.
