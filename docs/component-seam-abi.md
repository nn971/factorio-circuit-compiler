# Constrained component seam ABI

This document defines the physical composition contract for reusable Factorio circuit modules. It sits above the low-level electrical `AnchoredBlueprint` primitive from `device-anchoring.md` and now has an explicit bridge into physical layout optimization.

## Why this layer exists

Electrical compatibility alone does not make a reusable physical component. Public docks, component confinement, rigid module-to-module alignment, component-owned space, and adapter workspace are also part of the interface. The constrained ABI therefore treats boundary geometry as an explicit contract rather than allowing callers to choose arbitrary relay waypoints or floating coordinates.

## Blueprint-level composition rules

### Components own bounded regions

A `ConstrainedComponent` declares one or more rectangular `ComponentFootprint` regions. Every emitted blueprint entity centre must lie inside at least one declared region. Constituent regions may touch along boundaries but must not overlap in their interiors.

`ComponentFootprint` remains the lightweight blueprint-composition representation and constrains entity centres. Generators must still leave appropriate physical margin for the entities they place.

### Public anchors occupy declared boundary slots

Every surviving public anchor is associated with exactly one `BoundarySlot`:

```text
footprint + side + integer slot + slot pitch
```

`boundary_anchor(...)` derives the physical coordinate from that data. Interior or floating public anchors are invalid, and two public anchors may not resolve to the same physical coordinate, including corner aliases.

### Public anchors belong to ordered seams

Every public boundary anchor belongs to exactly one named `ComponentSeam`. A seam:

- lies on one side of one constituent footprint;
- contains one or more anchors;
- orders its lanes by increasing boundary slot;
- acts as the unit of physical composition.

One seam may not silently span several constituent cells of an already composed assembly.

### Seam composition derives the rigid translation

`compose_component_seams(...)` derives the right component's translation from corresponding seam lanes. Callers do not supply an arbitrary offset.

Composition succeeds only when:

- the seams face opposite directions;
- they contain the same number of lanes;
- corresponding low-level anchor contracts are compatible;
- every lane implies the same rigid translation;
- translated component interiors do not overlap existing interiors.

The composer merges exact-overlap terminals. It does not invent routing entities or cross-component wires.

### Repeated composition preserves constituent footprints

A composed assembly keeps each translated constituent footprint rather than collapsing the whole result into one bounding rectangle. This preserves per-cell confinement and lets another compatible component be appended without weakening the geometry contract.

## D1 physical-placement bridge

`factorio_circuit.synthesis.component_geometry` is the authoritative optimization-side representation introduced by Milestone D1. It deliberately does not replace `ConstrainedComponent`; the two layers serve different stages.

### Local rigid geometry

A `RigidComponentConstraint` records:

- one current component origin and quarter-turn orientation;
- the physical layout object ids that belong to the component and their rigid local offsets;
- one or more local `ComponentRegion` footprint rectangles;
- optional external keepout rectangles;
- optional reserved adapter rectangles;
- named `ComponentAccessPoint` positions on footprint boundaries;
- the origins and quarter-turn orientations that a future rigid-body placer may select.

All component regions and member offsets are expressed in local coordinates and transformed by the selected rigid pose. The current pose must already be one of the declared legal poses.

### Whole physical boxes, not just centres

The optimization-side validator uses the actual nominal physical half-extents known by synthesis. A component member is valid only when its complete physical box fits inside a declared footprint. This is intentionally stronger than the centre-only `ConstrainedComponent` composition check.

### Owned footprints and keepouts exclude other physical objects

A component's footprint and keepout regions are reserved from every physical object outside that component. An existing combinator or relay overlapping them makes the component optimization problem invalid.

The component's own members are allowed inside those owned/keepout regions. A physical object may belong to at most one rigid component.

### Adapter regions are currently empty reservations

Reserved adapter regions are different from component-owned space: in D1 they must contain no current physical object, including the component's own members. They exist so a later anchored-interface stage can materialize adapters or relay workspace without discovering that ordinary placement has already occupied the required area.

### D1 lowering is intentionally rigid and fail-safe

`ComponentLayoutOptimizationProblem` wraps an ordinary `LayoutOptimizationProblem` plus its rigid component constraints. `lower_component_layout_problem(...)`:

1. validates the selected component pose and exact member coordinates;
2. validates whole-box footprint confinement and exclusion regions;
3. adds every current component member to the ordinary exact `fixed_positions` contract;
4. removes component footprint, keepout, and adapter overlap sites from both unit and wide placement lattices.

The existing physical optimizer and fresh transactional router therefore cannot move an ordinary combinator or place a relay into component-reserved geometry. The ordinary exact layout validator, fixed-object handling, lattice legality, wire reach, and electrical-topology checks remain authoritative after lowering.

D1 freezes the current component pose on purpose. It does **not** approximate rigid motion by moving members independently. Milestone D2 will make the component itself a movable rigid macro using the already-declared allowed origins/orientations.

`optimize_component_layout(...)` performs the lowering, runs the existing fail-safe optimizer, and revalidates the resulting serialized layout against the component contract.

### Compiled modules may expose constrained seams after synthesis

The current implementation can wrap named ports of an already compiled module with `compiled_module_as_anchored_blueprint(...)`, then place those anchors on declared boundary slots and validate the resulting `ConstrainedComponent`.

Arbitrary distant pre-placement port pinning remains deferred to D3. D1 represents and protects adapter/access geometry, but it does not yet construct guaranteed relay workspace from a distant external anchor to that access point.

## Layering

```text
logical/device protocol
        ↓
exact-overlap AnchorSpec / AnchoredBlueprint
        ↓
compiled-module boundary adaptation when needed
        ↓
ConstrainedComponent
  blueprint footprint + boundary slot + ordered seam
        ↓
RigidComponentConstraint
  rigid members + whole-box footprints
  keepouts + adapter reservations + access points
        ↓
ComponentLayoutOptimizationProblem
        ↓
D1 lowering: fixed rigid pose + filtered legal lattice
        ↓
ordinary exact physical optimizer / fresh router
```

`AnchoredBlueprint` owns electrical compatibility. `ConstrainedComponent` owns blueprint-level seam composition. `RigidComponentConstraint` owns the optimizer-facing geometry. None of these layers weakens the lower-level electrical or exact physical checks.

## Current limitations after D1

The remaining ABI/placement work intentionally includes:

- movable rigid multi-entity components in the joint optimizer (D2);
- pre-placement public-port pinning for arbitrary distant anchors with guaranteed relay workspace (D3);
- materialization of actual adapter/routing objects inside reserved adapter regions;
- automatic conversion from every `ConstrainedComponent` blueprint into physical IR/layout member ids;
- through-bus/tap/contribution roles;
- seam-level signal/color allocation policy beyond the underlying anchor specs;
- arbitrary non-rectangular component regions.

Those capabilities should be added only when they have independent physical validation. D1 establishes the geometry contract and hard reservation behavior without reviving the earlier fragile post-hoc keepout/anchor experiments.
