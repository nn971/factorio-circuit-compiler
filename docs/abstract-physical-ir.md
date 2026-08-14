# Abstract Physical IR

The **Abstract Physical IR** is the target-specific boundary between logical optimization/state
realization and final Factorio placement.

Its job is to say exactly **what target combinators exist and which logical electrical relations they
need**, while leaving mutually dependent physical choices unresolved.

## Pipeline

```text
symbolic frontend
    ↓
semantic / logical IR
    ↓
logical optimization + state timing/realization
    ↓
AbstractPhysicalCircuit
  - exact target combinator operations
  - abstract signal-lane variables
  - abstract electrical nets
  - signal-allocation conflicts/aliases
  - net-merge conflicts
  - logical I/O endpoints and output phases
    ↓
physical synthesis
  - choose concrete Factorio signal identities
  - merge compatible logical nets
  - assign red/green wires
  - place entities
  - route reach-safe wires and relays
    ↓
Layout
  - final entity configurations
  - final coordinates
  - final red/green wiring
    ↓
blueprint generator
  - serialize Layout to Factorio blueprint JSON/string
```

`Layout` is the output object of physical synthesis rather than another optimization layer. Blueprint
generation serializes an already-final physical design.

## Abstract signals

`AbstractSignal` denotes a signal identity that still needs allocation. Its integer `id` is only an IR
identity. A signal may optionally restrict the target signal namespace (`virtual`, `item`, `fluid`, or
`any`).

Abstract signals deliberately do not belong to one net. A Factorio signal name is a lane identity,
while a wire network is an electrical-connectivity object. One physical network carries many signal
names, and the same signal name can occur on multiple electrically disconnected networks.

This separation permits signal reuse: two abstract signals may receive the same concrete Factorio
signal identity when physical synthesis proves that their synthesized electrical groups do not
overlap. `SignalConflict` forbids unsafe reuse and `SignalAlias` records identities that must be
materialized as the same concrete lane.

## Abstract nets

`AbstractNet` is one logical electrical-connectivity requirement among combinator connectors. It has
no red/green color yet. Its lane description has three parts:

- `signals`: compiler-allocated abstract signal lanes known to coexist on the net;
- `fixed_signals`: concrete target lanes chosen by the user/semantic program;
- `carries_dynamic_vector`: the net may carry arbitrary runtime lanes.

Several abstract nets may touch the same combinator connector. Physical synthesis can merge compatible
ones onto one electrical network, while incompatible ones occupy distinct red/green networks.
`NetConflict` records pairs that must remain electrically distinct.

Runtime-open vector nets are intentionally conservative merge boundaries: combining them with another
logical net can introduce unknown lane collisions.

## Combinator operands

Target combinator operations are already concrete at this layer: arithmetic and decider operations,
`Each`, selector behavior, constants, copy-count behavior, multi-output decider structure, and related
target semantics are fixed before physical synthesis.

Operands refer to abstract nets rather than concrete wire colors. This lets lowering express that two
inputs must remain electrically distinct without prematurely choosing red versus green.

## What physical synthesis owns

The Abstract Physical IR contains no compiler-chosen concrete `SignalId` allocation, red/green
orientation, final compatible-net merge decision, entity coordinates, or relay routing. User-selected
fixed target signals may appear because their identity is part of program semantics.

These choices interact strongly enough to remain in one physical-synthesis layer. For example, a
signal allocation can enable or prevent a net merge, and a net merge can reshape placement/routing.

## Current lowering

The canonical executable path is `compile_circuit(...)`. The lowerer currently supports:

- scalar inputs and fresh `InputSample` observations;
- whole-vector inputs and fresh `VectorInputSample` observations;
- scalar and whole-vector constants;
- direct fixed-lane `.signal(...)` views of vector nets;
- scalar arithmetic/comparisons/selects;
- runtime-open vector arithmetic/filtering/predicates/selectors;
- phase-alignment delays;
- conservative `Each` packing, including generic pairwise arithmetic packing;
- shared-predicate/multi-output-decider realization;
- `AccumulatorReg` and `FreezeReg`, including multicycle state timing.

Packed lanes use signal aliases/conflicts and net conflicts so the physical synthesizer, rather than
the logical lowerer, owns the eventual concrete signal and red/green choices.

## Current physical synthesis

`synthesize_layout(...)` / the canonical synthesis path currently:

1. derives additional hard net conflicts when a same-color merge would combine repeated known lanes or
   mix a runtime-open vector with another net;
2. bipartitions hard net conflicts onto the two Factorio wire colors and flips connected components to
   improve proven-safe local coalescing preferences;
3. coalesces same-color compatible nets that already meet at a connector into physical electrical
   groups;
4. colors the abstract-signal interference graph, reusing concrete virtual signal identities across
   electrically disjoint groups while reserving fixed user-selected signals;
5. materializes a concrete `PhysicalCircuit` view for simulation and annotation;
6. places entities with the default net-aware grid placer;
7. routes reach-safe wires, inserting layout-only relay entities where necessary;
8. retries deterministic placement basins, reducing target fill when routing needs more space;
9. returns the final `Layout`.

The placer treats synthesized electrical groups as hyperedges and optimizes approximate reach
connectivity, relay count, and spanning-tree length. It can reserve regular computation blocks
separated by walking/power corridors; 2x2 footprints at corridor crossings are kept free for future
substation emission. Row placement remains available as a compatibility/debugging strategy.

Placement and routing are still heuristic. A good approximate hyperedge/MST score can produce a layout
that is awkward for the concrete point-to-point relay router, especially on the autonomous-market
controller. Future geometry work may feed failed routes back into placement or route a whole electrical
group jointly, but target-graph reductions should be preferred when they remove the underlying
physical complexity.

## State realization

`AccumulatorReg` and `FreezeReg` lower through abstract nets rather than concrete wire colors. Vector
memory/data paths and scalar control paths receive `NetConflict` metadata where electrical separation
is required.

`StateTimingPlan` is computed before abstract physical lowering. State-derived scalar/vector logic
contributes its physical latency to recurrence constraints, and multicycle domains receive synthesized
periodic commit gating. The synthesizer then resolves wire colors and concrete signal identities while
preserving the state realization's electrical constraints.

This keeps source semantics independent from whichever red/green orientation happens to be chosen in a
particular layout.

## Backend compatibility

`compile_circuit(...)` is canonical. `compile_abstract_circuit(...)` remains a compatibility alias for
existing tests/callers. The previous direct-concrete backend survives in
`factorio_circuit.compiler_legacy.compile_legacy_circuit(...)` only as a P=1 parity/debugging oracle;
it is not a second production pipeline.
