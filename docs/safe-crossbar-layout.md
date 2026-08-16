# Safe crossbar physical layout

## Purpose

`safe-crossbar` is the compiler's constructive physical-layout fallback. It exists to separate two
questions that were previously entangled:

1. can a supported physical circuit always be materialized into a valid Factorio blueprint?;
2. can that blueprint be compact and relay-efficient?

The safe crossbar answers only the first question. It deliberately spends map area and blank constant
combinators so placement and routing never need heuristic search.

The first implementation is used by `examples.snake_blueprint` because the previous row and spacious
greedy layouts could still exhaust the collision-aware router on the full Snake circuit.

## Construction

All real implementation entities are placed on one sparse horizontal row at six-tile intervals:

```text
C0      C1      C2      C3      ...
```

Physical electrical groups are built directly from the synthesis-stage `net_groups` result rather than
first being flattened into geometry-selected spanning-tree edges.

- every RED physical group receives one horizontal bus above the entity row;
- every GREEN physical group receives one horizontal bus below the entity row;
- every concrete connector/color incidence receives one vertical feeder from the entity to its bus.

INPUT and SINGLE connectors use the feeder column two tiles left of the entity centre. OUTPUT
connectors use the column two tiles right of the entity centre. Because entity centres are spaced by
six tiles, all feeder columns are globally distinct.

High-fanout groups are ordered closest to the entity row. This minimizes weighted feeder length without
changing any correctness invariant.

## Six-tile lattice

The default compiler wire span is conservatively seven tiles. Safe-crossbar uses a six-tile relay pitch.
Its longest local entity-to-first-feeder hop is

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

Different feeders are globally distinct columns. Different buses are distinct rows. RED and GREEN
relay systems are separated into opposite half-planes. Consequently the construction never needs to
ask whether a candidate relay position is free.

The implementation asserts that no formula-generated relay coordinate is assigned to two distinct
physical groups.

## Routing algorithm

For every multi-endpoint physical group:

1. assign its deterministic bus row;
2. construct one tap for every endpoint;
3. connect the real endpoint to the first feeder relay;
4. emit feeder relays every six tiles until the tap;
5. emit ordinary bus relays at six-tile x positions between the leftmost and rightmost taps;
6. sort all bus relays and taps by x and connect adjacent nodes.

The normal `route_wires()` implementation, parallel-lane search, grid fallback, placement annealing, and
retry loop are not called.

For physical simulation, `PhysicalCircuit.connections` still contains a relay-free deterministic chain
between the real endpoints of each physical group. The `Layout` contains the actual reach-safe relay
geometry used by blueprint serialization.

## Complexity

Let

- `V` be real physical entities;
- `I` be endpoint/net incidences;
- `R` be generated relay entities;
- `W` be emitted blueprint wire segments.

The construction is output-sensitive. Apart from sorting endpoints/groups and bus nodes, work is
proportional to the generated structure. There is no A*, collision backtracking, annealing, or random
restart factor.

The relay count may be large and in bad cases may grow superlinearly with the original circuit because
farther bus rows require longer endpoint feeders. That is an accepted cost of the fallback.

## Supported subset and deliberate limitations

The first implementation assumes:

- compiler-generated arithmetic, decider, and constant combinators with the existing horizontal target
  orientation;
- blank constant combinators as wire relays;
- the compiler's synthesized red/green physical-net grouping is valid;
- no fixed user placement anchors;
- a configured safe wire span of at least `sqrt(40)` tiles.

It does not attempt compactness, walking corridors, substation placement, device-aware placement, or
optimized net routing.

The next physical-synthesis milestone may optimize physical-net routing and layout quality. Safe-crossbar
should remain as the correctness baseline against which those optimizers are tested.

## Snake

The recommended first-playtest command now uses safe-crossbar by default:

```bash
uv run python -m examples.snake_blueprint
```

The old strategies remain available for diagnostics and future optimization work:

```bash
uv run python -m examples.snake_blueprint --greedy-layout
uv run python -m examples.snake_blueprint --net-aware-layout
uv run python -m examples.snake_blueprint --row-layout
```
