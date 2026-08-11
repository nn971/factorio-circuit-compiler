# Architecture

Keep the architecture deliberately small:

```text
ordinary Python elaboration
        ↓
symbolic frontend
  Circuit / Input / SignalsInput / Expr / state objects
        ↓
logical circuit
  - stateless expression DAG
  - source-sample provenance and freshness offsets
  - explicit built-in state accesses/updates
        ↓
optimization + state realization
        ↓
abstract physical Factorio IR
  - exact target combinator behavior
  - abstract signal variables
  - abstract electrical nets
  - compatibility/conflict metadata
        ↓
physical synthesis
  - concrete signal allocation
  - compatible-net merging
  - red/green assignment
  - final entity placement and reach-safe wiring
        ↓
Layout
        ↓
blueprint serialization + encoding
```

The abstract physical IR is target-specific rather than a separate architecture/design layer. It
exists so signal allocation, electrical-net choices, and placement can be optimized jointly during
physical synthesis.

## Symbolic frontend responsibilities

Python runs once as elaboration. Symbolic operators create logical IR nodes immediately.

- `Circuit.input(name)` creates a scalar external source.
- `Circuit.signals(name)` creates a whole-signal-vector external source.
- source `.sample()` creates a fresh observation at the current freshness cursor.
- scalar operators create `BinaryOp`, `Compare`, and `Select` nodes.
- `Circuit.output(name, expr)` declares an observable output.
- state objects create explicit read/update IR records.

Derived `Expr` objects represent logical streams and intentionally expose no physical execution tick.

## Logical circuit responsibilities

The logical circuit records:

- external source identities;
- fresh source observations such as `InputSample(source=x, offset=3)`;
- arithmetic/comparison/mux dependencies;
- state observation freshness;
- strict v1 state-access order identities;
- state update requests;
- whole-vector constant sources and concrete signal-lane observations;
- named outputs.

Physical phases are inferred later.

## Stateless optimization

The stateless DAG remains the main input to simplification, CSE/DCE, compatibility partitioning,
conservative `Each` packing, phase/alignment scheduling, and late signal allocation.

Values with different freshness origins may feed one operation. The physical scheduler delays older
values until the required samples coexist without changing sample identity.

## State realization

Built-in state components remain opaque enough that the backend can choose Factorio-native
implementations.

Current trusted prototypes:

- `AccumulatorReg`;
- `FreezeReg`.

The IR records freshness/order metadata for state accesses. `analysis/state_timing.py` turns that
metadata into one `StateTimingPlan`: semantic commit offsets, physical register phases, transition-input
alignment phases, and read phases. The physical state lowerer consumes the plan. The current solver
handles the trusted one-compound-transition vector registers together, including mutually coupled
state-to-state vector feeds. Register phases are solved as difference constraints; a positive-latency
feedback cycle is diagnosed as an initiation-interval limitation of the current physical prototypes.

## Abstract physical IR

`ir/abstract_physical.py` represents exact target combinator behavior while keeping late physical
resources unresolved. `AbstractSignal` is a signal-lane variable rather than a concrete `SignalId`.
`AbstractNet` is a logical electrical-connectivity requirement with no red/green color. Signals and
nets are independent: one net may carry many signals, and one abstract signal may occur on multiple
electrically disconnected nets.

Each `AbstractNet` records compiler-allocated lanes, fixed concrete lanes, and whether it may carry an
open runtime vector. Pairwise `SignalConflict` metadata forbids unsafe abstract-signal aliasing when
lanes coexist on one net. `NetConflict` forbids electrical net merges. Compatible choices remain
deliberately unresolved.

The executable lowerer now covers scalar and whole-vector stateless circuits plus both trusted vector
registers: fresh scalar/vector sampling, vector constants, isolating `.signal(...)` extraction,
phase-alignment delays, arithmetic, comparisons, selects, conservative `Each` packing,
`AccumulatorReg`, and `FreezeReg`. Register vector/control separation is expressed with abstract net
conflicts rather than wire colors. See `docs/abstract-physical-ir.md` for the detailed contract.

## Physical synthesis and Layout

Physical synthesis consumes the abstract physical IR and jointly chooses concrete Factorio signal
identities, compatible net merges, red/green assignment, and final placement/wiring. Its output is a
`Layout` object containing the final placement choices and reach-safe physical wiring.

Blueprint generation is downstream serialization: it translates `Layout` to Factorio blueprint JSON,
then performs the standard compression/base64 encoding. Layout is an output data object of physical
synthesis, rather than another processing layer.

`compile_abstract_circuit(...)` is the executable reference path for this architecture. Its physical
synthesizer allocates unique virtual signals, two-colors explicit net conflicts, and may flip whole
conflict components to favor compatible same-color coalescing at connectors already shared by multiple
abstract nets. Those unavoidable electrical merges are recorded explicitly in `Layout`; disjoint nets
are not globally joined yet. The synthesizer then reuses the current deterministic row placement and
reach-safe routing. Blueprint serialization consumes that completed layout without choosing geometry
or wiring. Scalar I/O annotation markers are also finalized here, after their concrete signals are
known.

The current `compile_circuit(...)` concrete backend remains the default while the new backend is
validated. Both trusted vector-state primitives have moved to the new path, and switchable Fibonacci
now serves as the coupled-state migration regression.
