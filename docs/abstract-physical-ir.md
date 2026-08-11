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
  - signal-allocation conflicts
  - net-merge conflicts
  - logical I/O endpoints and output phases
    ↓
physical synthesis
  - choose concrete Factorio signal identities
  - merge compatible logical nets
  - assign physical nets to red/green wires
  - place entities and any required relay/pole infrastructure
  - optimize these choices jointly
    ↓
Layout
  - final entity configurations
  - final coordinates
  - final red/green wiring
    ↓
blueprint generator
  - serialize Layout to Factorio blueprint JSON
  - compress/base64 encode blueprint string
```

`Layout` is the **output object of physical synthesis**, rather than another optimization layer.
Blueprint generation is serialization/encoding of an already-final physical design.

## Abstract signals

`AbstractSignal` denotes a signal identity that still needs allocation. Its integer `id` is only an IR
identity. A signal may optionally restrict the target signal namespace (`virtual`, `item`, `fluid`, or
`any`).

Abstract signals deliberately do not belong to one net. A Factorio signal name is a lane identity,
while a wire network is an electrical connectivity object. One physical network carries many signal
names, and the same signal name can occur on multiple electrically disconnected networks.

This separation also permits later signal reuse: two abstract signals may receive the same concrete
Factorio signal identity when physical synthesis proves that their electrical lifetimes cannot
interfere. `SignalConflict` records pairs for which such aliasing is forbidden.

## Abstract nets

`AbstractNet` is one logical electrical-connectivity requirement among combinator connectors. It has
no red/green color yet. Its lane description has three parts:

- `signals`: compiler-allocated abstract signal lanes known to coexist on the net;
- `fixed_signals`: concrete target lanes chosen by the user/semantic program, such as `iron-plate`;
- `carries_dynamic_vector`: the net may also carry arbitrary runtime lanes, as a whole-vector external
  input does.

This lets physical synthesis reason about signal aliasing and net merging without pretending a dynamic
Factorio signal map has a finite compiler-known lane set.

Several abstract nets may touch the same combinator connector. This is intentional: physical synthesis
may discover that some can be merged into one electrical network, while incompatible ones must occupy
separate red/green networks (or force another realization/placement choice).

`NetConflict` records pairs that must remain electrically distinct. Compatible pairs are candidates for
merging; physical synthesis is free to merge them when doing so helps placement or signal allocation.

## Combinator operands

Target combinator operations are already concrete at this layer: arithmetic and decider operations,
`Each`, constants, copy-count behavior, and similar target semantics are fixed.

A scalar/`Each` operand can additionally name the abstract nets from which that input is intended to be
observed. These are logical network selections, replacing the current early commitment to
`networks=(RED, GREEN)`.

## What is deliberately absent

The Abstract Physical IR contains no **compiler-chosen** concrete `SignalId` allocation, red/green
wire-color selection, final net-merging decision, entity coordinates, or relay placement/wire-span
repair. User-selected fixed target signals may appear because their identity is part of program
semantics rather than a synthesis choice.

Those choices interact strongly enough that they belong to one **physical synthesis** layer. Keeping
them together also leaves room for future layout-aware optimizations such as preferring a net merge
because it removes a wire, or preferring a different signal allocation because it permits such a
merge.

## Implemented baseline

The executable path is available through `compile_abstract_circuit(...)`.
`lower_abstract_physical(...)` supports:

- scalar inputs and fresh `InputSample` observations;
- whole-vector inputs and fresh `VectorInputSample` observations;
- scalar and whole-vector constants;
- `.signal(...)` extraction from vectors through an isolating combinator;
- arithmetic and comparisons;
- scalar `Select` lowering;
- phase-alignment delays;
- conservative compatible `Each` packing;
- `AccumulatorReg`, including vector feedback, add gating, clear control, and state timing;
- `FreezeReg`, including pass/hold controls, vector feedback, and state timing.

The lowerer records compatibility rather than selecting physical resources. Packed output nets carry
multiple abstract signals and add pairwise `SignalConflict` records. A scalar consumer that must keep a
multi-lane source electrically distinct from another source adds `NetConflict`; it does not choose red
or green.

The baseline `synthesize_layout(...)` then:

1. allocates a unique virtual Factorio signal to every abstract signal while reserving all fixed
   concrete signal identities already used by the circuit;
2. bipartitions every hard `NetConflict` component, then flips whole components when doing so
   increases same-color coalescing among compatible nets that already meet at a connector;
3. records those unavoidable same-color shared-connector merges as physical net groups while
   preserving each abstract net's local wiring tree;
4. materializes a concrete `PhysicalCircuit` view for simulation and fills scalar I/O annotation
   descriptions with their final allocated signal identities;
5. applies the current deterministic row placement and reach-safe relay routing;
6. returns a final `Layout` containing positions, routed wires, relays, signal allocation, net
   colors, and abstract-net-to-physical-group mapping.

This first net-merging optimization is intentionally local. It never connects otherwise disjoint
compatible nets merely to create a larger bus: doing that can add wire length/relays and must be
decided together with placement/routing in a later optimization.

`blueprint/layout_encode.py` serializes only this completed `Layout`, so blueprint generation no longer
makes placement or routing decisions on the new path.

## Accumulator state migration

`AccumulatorReg` now lowers through abstract physical nets rather than concrete wire colors. The
register memory loop is represented as one runtime-vector `AbstractNet` touching the memory input,
memory output, gated-add outputs, and state consumers. Scalar add/clear controls remain separate nets.
Whenever one combinator must observe vector data and scalar control independently, lowering emits a
`NetConflict` between those nets instead of selecting red/green.

`StateTimingPlan` is computed before abstract physical lowering, just as in the legacy path. Accumulator
source vectors are delayed to the transition-input phase with target-level `Each + 0` delay combinators,
and `VectorRegisterRead` carries the timing plan's physical read phase into the abstract output.

The synthesizer then resolves the accumulator's conflict graph. Vector data and scalar control land on
opposite wire colors, reproducing the trusted concrete prototype without embedding a particular
red/green orientation in state lowering. Whole conflict components may be flipped later when that
improves compatible net coalescing elsewhere.

## Freeze state migration

`FreezeReg` now follows the same boundary. Its input vector and pass control remain separate abstract
nets at the transparent gate; its feedback vector and hold control remain separate at the memory cell.
Those pairs receive `NetConflict` records rather than concrete colors. The memory input/output are one
runtime-open feedback net reserved before state lowering, which also allows another register to use it
as a transition source.

The switchable Fibonacci regression couples a `FreezeReg` and an `AccumulatorReg` through those reserved
feedback nets, then extracts one fixed lane from both post-transition reads. It produces
`1, 1, 2, 3, 5`, holds while disabled, and resumes at `8, 13` through the new backend.

## Current migration boundary

`compile_circuit(...)` still uses the previous direct concrete lowerer and remains the default comparison
path. Scalar/whole-vector stateless behavior, `.signal(...)` isolation, `AccumulatorReg`, `FreezeReg`,
and mutually coupled vector state now have an executable abstract-physical path. The next backend work
should focus on parity coverage and on physical-synthesis optimizations that jointly use signal, net,
and layout freedom.
