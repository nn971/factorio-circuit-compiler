# Constrained component seam ABI

This document defines the physical composition contract for reusable Factorio circuit modules. It sits above the low-level electrical `AnchoredBlueprint` primitive from `device-anchoring.md`.

## Why this layer exists

Electrical compatibility alone does not make a reusable physical component. Public docks, component confinement, and rigid module-to-module alignment are also part of the interface. The constrained ABI therefore treats boundary geometry as an explicit contract rather than allowing callers to choose arbitrary relay waypoints or floating coordinates.

## Normative rules

### Components own bounded regions

A `ConstrainedComponent` declares one or more rectangular `ComponentFootprint` regions. Every emitted blueprint entity centre must lie inside at least one declared region. Constituent regions may touch along boundaries but must not overlap in their interiors.

`ComponentFootprint` currently constrains entity centres rather than maintaining a prototype-specific collision-box catalogue. Generators must still leave appropriate physical margin for the entities they place.

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

### Compiled public ports participate in placement

For compiler-generated reusable components, supply final named dock coordinates with:

```python
compile_circuit(..., port_positions={...})
```

The compiler converts those names into ordinary anchored placement constraints before physical synthesis. The current annealed layout path therefore sees the final public boundary while placing implementation logic.

Post-compilation anchor adaptation remains useful for electrical isolation/renaming at the boundary, but long arbitrary adapter routes should not be the normal way to define a component's external geometry.

## Layering

```text
logical/device protocol
        ↓
exact-overlap AnchorSpec / AnchoredBlueprint
        ↓
ConstrainedComponent
  footprint + boundary slot + ordered seam
        ↓
named compiler ports pinned before placement
        ↓
annealed physical placement and internal routing
        ↓
exact seam composition
```

`AnchoredBlueprint` owns electrical compatibility. `ConstrainedComponent` adds geometry and composition invariants; it does not weaken or replace the electrical checks.

## Current limitations

The extracted ABI intentionally does not yet provide:

- prototype-specific collision-box confinement for whole component regions;
- hard component keepouts consumed by the annealer;
- automatic derivation of reserved adapter areas during placement;
- through-bus/tap/contribution roles;
- seam-level signal/color allocation policy beyond the underlying anchor specs;
- arbitrary non-rectangular component regions.

Those capabilities should be added only when they have independent physical validation. In particular, the abandoned hard-keepout/strict-adapter experiment is not part of this extracted mainline contract.
