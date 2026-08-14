# Architecture

```text
ordinary Python elaboration
        ↓
symbolic frontend
  Circuit / Input / SignalsInput / Expr / state objects
        ↓
logical circuit
  - scalar/vector stream graph
  - logical source-sample offsets
  - explicit state accesses/updates
        ↓
optimization + logical clock-domain timing
        ↓
abstract physical Factorio IR
  - exact target combinators
  - abstract signals and electrical nets
  - compatibility/conflict metadata
        ↓
physical synthesis
  - concrete signals
  - red/green assignment
  - placement and reach-safe wiring
        ↓
Layout
        ↓
blueprint serialization
```

The abstract physical IR is target-specific. It exists so signal allocation, electrical-net choices,
and placement can be optimized jointly during physical synthesis.

## Symbolic frontend

Python runs once as elaboration. Symbolic operators create logical stream nodes.

- `Circuit.input(name)` and `Circuit.signals(name)` create external physical sources.
- input/register `.sample()` observes a source at the current logical step.
- `Circuit.step(n)` advances the logical observation cursor.
- `Circuit.tick()` is reserved for future physical-tick constraints.
- scalar/vector operators create logical expressions without exposing physical execution ticks.
- state objects create explicit read/update IR records.

Derived expressions are already sampled logical streams and therefore have no `.sample()` operation.

## Logical timing and state realization

Logical step and physical game tick are separate coordinates. Each connected state clock domain has
an inferred physical period `P`; a value phase `phi` realizes logical value `v[k]` at
`phi + k*P`.

Stateless operations preserve `k` and add physical latency. Feed-forward latency does not constrain
`P` because pipelines can overlap samples. Recurrences do constrain `P`.

Ordinary state dependencies union their registers into one domain. Independent state components may
have different periods. Explicit state communication between different periods is future
clock-domain-crossing work rather than ordinary same-index arithmetic.

`analysis/state_timing.py` records each dependency as logical displacement plus physical latency. For
source offset `r`, target commit offset `c`, latency `L`, and shared period `P`:

```text
phi_target >= phi_source + (r - c - 1) * P + L + 1
```

The analyzer chooses the smallest feasible positive integer `P` per domain and then solves register
phases as difference constraints. A positive-latency recurrence with positive logical distance is
therefore legal at a sufficiently large period. A positive-latency cycle with zero logical distance
remains impossible.

The physical lowerer consumes this plan. For `P>1` it synthesizes a modulo-domain clock and gates
`AccumulatorReg`/`FreezeReg` transitions so intermediate Factorio ticks retain state.

Register accesses still carry strict elaboration order. Reads cannot split one compound transition;
post-transition observations use a later logical step.

## Abstract physical IR

`ir/abstract_physical.py` represents exact target combinator behavior while keeping late physical
resources unresolved. `AbstractSignal` is a signal-lane variable rather than a concrete `SignalId`.
`AbstractNet` is an electrical-connectivity requirement with no red/green color.

Nets distinguish compiler-allocated lanes, user-fixed concrete lanes, and runtime-open vectors.
`SignalConflict`, `SignalAlias`, and `NetConflict` express allocation/electrical constraints without
prematurely choosing concrete signals or wire colors.

The canonical lowerer covers scalar logic, runtime-open vectors, fresh logical samples, selector
operations, phase-alignment delays, `AccumulatorReg`, and `FreezeReg`.

## Physical synthesis and Layout

Physical synthesis jointly chooses:

- concrete Factorio signal identities;
- compatible net merges;
- red/green allocation;
- final placement and reach-safe wiring.

Its output is `Layout`. Blueprint generation is downstream serialization only; it does not choose
geometry or wiring.

`compile_circuit(...)` is the canonical path. `compile_abstract_circuit(...)` remains a compatibility
alias. `compiler_legacy` is only a P=1 comparison/debugging oracle and explicitly rejects multicycle
state domains.
