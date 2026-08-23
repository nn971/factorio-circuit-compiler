# Constrained component seam ABI

This document defines the physical composition conventions for reusable Factorio circuit modules.
It sits above the low-level electrical `AnchoredBlueprint` primitive.

## Why this layer exists

An electrically valid anchor is not enough to make a reusable physical component. A caller that can
choose arbitrary coordinates and arbitrary relay waypoints can create a correct but unmaintainable
blueprint whose implementation leaks across neighboring modules. The constrained ABI therefore treats
geometry as part of the component contract.

## Normative rules

### 1. Components own bounded physical regions

A constrained component MUST declare one or more rectangular `ComponentFootprint` regions. Every
ordinary blueprint entity centre emitted by the component MUST lie inside at least one declared
region. Distinct regions in one assembly MUST NOT overlap in their interiors; touching boundaries are
allowed for exact seam composition.

`ComponentFootprint` currently constrains entity centres, not prototype collision boxes. Component
generators remain responsible for choosing enough margin for their real entity footprints.

### 2. Public anchors live on declared boundary slots

A constrained boundary anchor MUST be identified by:

- one component side: north, east, south, or west;
- one non-negative integer slot;
- one owning footprint.

The physical anchor coordinate is derived from `(footprint, side, slot, slot_pitch)`. Application code
SHOULD use `boundary_anchor(...)` rather than supplying a raw `(x, y)` coordinate.

A constrained component MUST cover every surviving public anchor with exactly one boundary slot.
Interior/floating anchors are invalid.

### 3. Public anchors belong to ordered named seams

Every constrained boundary anchor MUST belong to exactly one `ComponentSeam`. A seam:

- has one stable name;
- lies on one side;
- contains one or more anchors;
- orders those anchors by increasing boundary slot.

A seam is the unit of physical composition. Callers should compose a seam, not manually align a set
of unrelated terminals.

### 4. Seam composition derives translation

`compose_component_seams(...)` MUST derive the right component's rigid translation from matching seam
lanes. Callers do not supply a manual `right_offset` in the constrained API.

The composition is valid only when:

- the two seams face opposite directions;
- they contain the same number of lanes;
- each corresponding low-level anchor pair is electrically compatible;
- every lane implies exactly the same rigid translation;
- translated component regions do not overlap existing component interiors.

The composer merges exact-overlap terminals. It MUST NOT invent routing entities or cross-component
wires.

### 5. Repeated composition preserves cell boundaries

A composed constrained assembly keeps the translated footprint of every constituent cell rather than
collapsing them into one giant rectangle. This allows repeated modules to remain physically
independent and lets later seam composition append another cell without weakening confinement.

### 6. Compiled interfaces participate in placement

For compiler-generated components, final public dock coordinates SHOULD be supplied to
`compile_circuit(..., port_positions=...)` before physical synthesis. Named compiler I/O marker
entities are then pinned in the abstract physical graph and the normal net-aware/annealing placer
optimizes implementation logic around those fixed docks.

Do not first generate an arbitrary finished layout and then route long adapter chains to distant ABI
coordinates unless a legacy component makes that unavoidable.

### 7. Safe crossbars are fallback/debug layouts

`safe-crossbar` and `safe-folded-crossbar` prioritize guaranteed constructive routing. They are useful
for correctness fallback and stress/debug work, but SHOULD NOT be the default layout policy for a
human-facing reusable component when the net-aware annealing placer can be used.

## Layering

The intended layering is:

```text
logical/device protocol
        ↓
constrained component ABI
  footprint + side/slot + seam
        ↓
compiler I/O pinned before placement
        ↓
net-aware / annealing physical placement
        ↓
component-owned internal routing
        ↓
exact seam composition
```

`AnchoredBlueprint` remains the low-level electrical primitive. `ConstrainedComponent` adds geometry
and composition invariants; it does not replace the electrical checks.

## Current limitations

The first implementation intentionally does not yet model:

- prototype-specific collision boxes;
- explicit through-bus/tap/contribute roles;
- seam-level red/green multiplexing policy beyond the underlying anchor specs;
- automatic compiler signal/color allocation to eliminate all interface isolation adapters;
- arbitrary non-rectangular component regions.

These are later extensions. The urgent invariant is that reusable modules no longer expose arbitrary
floating coordinates or composer-generated wandering relay routes as their normal physical ABI.
