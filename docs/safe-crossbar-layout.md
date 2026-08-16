# Safe crossbar physical layout

## Purpose

`safe-crossbar` is the compiler's constructive physical-layout fallback. It separates two questions
that were previously entangled:

1. can a supported physical circuit be materialized without routing search?;
2. can that blueprint be compact and relay-efficient?

Safe-crossbar answers only the first question. It deliberately spends map area and blank constant
combinators so placement and routing never need A*, collision backtracking, annealing, or retries.

The first implementation assigned one globally unique bus row to every physical electrical group.
Snake exposed why that is too pessimistic: 4,623 physical groups produced more than twenty million
relays because progressively farther bus rows made endpoint feeders grow roughly quadratically. The
current implementation keeps the constructive guarantee but **reuses bus tracks for disjoint net
intervals**.

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

The important size parameter is therefore no longer the total number of physical nets. It is the
**maximum same-color interval overlap**, i.e. the routing cutwidth induced by the current entity order.

## Six-tile lattice

The default compiler wire span is conservatively seven tiles. Safe-crossbar uses a six-tile relay
pitch. Its longest local entity-to-first-feeder hop is

```text
sqrt(2^2 + 6^2) = sqrt(40) ~= 6.325 tiles
```

so the current implementation requires

```text
blueprint_safe_wire_span >= sqrt(40)
```

and works with the default value `7.0`.

The lattice phases are:

```text
real entity centres:       x = 0 mod 6, y = 0
INPUT/SINGLE feeders:      x = -2 mod 6
OUTPUT feeders:            x = +2 mod 6
ordinary feeder relays:    y = 0 mod 6, excluding y = 0
bus rows:                  y = 3 mod 6
ordinary bus relays:       x = 0 mod 6
owning taps:               feeder x on owning bus y
```

RED buses/feeders occupy only `y < 0`. GREEN buses/feeders occupy only `y > 0`.

## Why unrelated nets do not short

Consider a vertical feeder crossing a foreign horizontal bus of the same color. The feeder is at
`x = +/-2 mod 6`, while ordinary bus relays exist only at `x = 0 mod 6`. Therefore the foreign crossing
has no relay entity. The wire segments may geometrically cross, but the electrical networks remain
disconnected.

The feeder's owning bus receives an explicit tap relay at the crossing. Only that crossing joins the
feeder to a bus.

Different feeders are globally distinct columns. Different simultaneously overlapping buses use
distinct track rows. Groups that reuse one track have disjoint relay intervals with at least the
configured center clearance. RED and GREEN relay systems live in opposite half-planes.

The implementation asserts that no formula-generated relay coordinate is assigned to two distinct
physical groups.

## Exact preflight and safety cap

Because track assignment happens before relay allocation, the relay count is known exactly before any
`LayoutRelay` objects are created.

For a group on track `t`:

- every endpoint contributes one tap and `t` ordinary feeder relays;
- ordinary bus relays occur every six tiles between the group's leftmost and rightmost feeder columns.

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
3. construct one tap for every endpoint;
4. connect the real endpoint to the first feeder relay;
5. emit feeder relays every six tiles until the tap;
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

Safe-crossbar should remain the correctness baseline against which later optimizers are tested.

## Snake

The recommended first-playtest command uses safe-crossbar by default:

```bash
uv run python -m examples.snake_blueprint > snake-blueprint.txt
```

Redirecting stdout is recommended for any large physical build so a long blueprint string is not sent
to the terminal. Progress and synthesis diagnostics remain on stderr.

The old strategies remain available for routing/layout experiments:

```bash
uv run python -m examples.snake_blueprint --greedy-layout
uv run python -m examples.snake_blueprint --net-aware-layout
uv run python -m examples.snake_blueprint --row-layout
```
