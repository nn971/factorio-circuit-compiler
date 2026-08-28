# Milestone D3 — anchored interface routing

D3 makes distant public compiler anchors physical constraints **before** fresh routing instead of
repairing the serialized blueprint afterward.

## Contract

`AnchoredInterfaceLayoutProblem` wraps the D1/D2 `ComponentLayoutOptimizationProblem` with one or
more `PublicPortAnchorConstraint` values. Each constraint names:

- an existing compiler input or output port;
- one rigid component;
- one named `ComponentAccessPoint` on that component boundary;
- the exact public marker position required by the external ABI;
- a bounded deterministic detour allowance for interface relay workspace.

The named port must resolve to an annotation marker on exactly one physical electrical group. That
same group must enter the declared rigid component through at least one component member, and a
member terminal must be within ordinary wire reach of the declared access point. This prevents a
geometric seam declaration from silently referring to an unrelated electrical net.

## Reservation before global routing

`route_anchored_interfaces_transactionally(...)` performs the following transaction:

1. validate the incoming exact component artifact;
2. discard the old relay scaffold;
3. move each public marker to its exact requested anchor and make that marker fixed;
4. generate same-phase candidate relay sites in bounded dogleg corridors toward the declared
   component access point;
5. choose a legal gateway near the access point and construct a reach-safe relay chain from the
   distant marker to that gateway;
6. make every interface relay fixed **before** routing any remaining physical net;
7. fresh-route the rest of the circuit around those fixed reservations;
8. simplify only non-fixed relays;
9. exact-validate component geometry, lattice legality, overlap, wire reach, electrical topology,
   exact marker pins, and the reserved relay memberships.

Failure at any stage returns the original exact-valid component problem unchanged.

The fixed interface chain is intentionally stronger than a post-hoc waypoint hint. Later physical
optimization sees the marker and interface relays through the ordinary `fixed_positions` contract,
so it cannot move an implementation object into the reserved path and cannot simplify the path
away.

## Relationship to D1 adapter regions

D1 adapter regions remain empty reservations in D3. D3 does not weaken that invariant: interface
relays are legalized outside component footprints, keepouts, and adapter regions, while still being
forced through the named boundary access geometry. Materializing protocol-specific adapter entities
inside those reserved rectangles remains application work for D4 or a later concrete ABI need.

## Scope

D3 does not add speculative seam roles and does not rotate rigid components. It also does not replace
the blueprint-level `AnchoredBlueprint` exact-overlap contract. D3 owns compiler-side pre-routing
pins and their guaranteed physical relay reservation; the existing device anchor layer still owns
electrical compatibility between independently generated blueprints.
