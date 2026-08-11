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
physical Factorio circuit
  - exact combinators
  - exact signals
  - exact red/green networks
        ↓
reach-safe layout + blueprint serialization
```

There is no separate architecture IR at present.

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

## Physical circuit

The physical representation contains exact combinator configuration, signal allocation, red/green
connector wiring, implementation pipeline delays, and feedback circuits. Geometry and long-wire relay
insertion remain utility-layer concerns after logical synthesis.
