# Compiler pipeline

This document is the implementation map. `data-contract.md` defines the logical meaning that these stages preserve.

## Overview

```text
ordinary Python elaboration
    -> symbolic frontend
    -> semantic CircuitModule
    -> causality / timing analysis
    -> Level or Event physical lowering
    -> AbstractPhysicalCircuit
    -> physical synthesis
    -> Layout
    -> blueprint serialization
```

`compile_circuit()` is the canonical orchestration entry point.

## 1. Symbolic frontend

Main files:

```text
frontend/symbolic.py
frontend/vector_circuit.py
frontend/vector_expr.py
frontend/reindex.py
frontend/clock_bridges.py
```

Python executes once as elaboration. `Circuit`, expressions, state handles, Event handles, and overloaded operators build immutable semantic records. The frontend owns user-facing validation such as same-circuit ownership, explicit Event crossings, and elaboration order.

The frontend may retain small compatibility wrappers for source ergonomics. Compatibility representation must end at the frontend-to-IR boundary rather than leaking into analysis or physical synthesis.

## 2. Semantic IR and normalization

Main files:

```text
ir/semantic.py
ir/state.py
ir/clocks.py
ir/output.py
lowering/frontend_to_ir.py
```

`CircuitModule` is the logical module boundary. Canonical values carry `Flow` metadata: payload shape, modality, structural clock, and logical offset. State is represented by explicit register/update records, clock crossings are explicit nodes, and output materialization is boundary metadata.

Level expressions are contextualized to their consumer-selected structural clock during frontend normalization. Event expressions already carry occurrence clocks from construction and explicit derived-clock/bridge objects.

A canonical semantic pass may rewrite logical expressions only when it preserves Flow/clock meaning. Event semantic optimization is currently conservative because old dense-stream transforms do not yet carry general clock proofs.

## 3. Logical analysis

Main files:

```text
analysis/causality.py
analysis/state_timing.py
analysis/latency.py
```

Causality operates in logical occurrence coordinates. Dependency edges record logical displacement and structural clock relation; target combinator latency is a later concern.

Timing then attaches Factorio latency. Periodic state analysis groups connected registers into domains and solves for the smallest feasible period/phases. Event state analysis derives required minimum separation and validates the authoritative clock contract.

Important boundary:

```text
causality error  = logical recurrence is invalid
throughput error = logical recurrence is valid but arrivals may be too fast
```

## 4. Physical lowering

The current compiler has two implementation lanes that share one semantic contract and converge on the same Abstract Physical IR.

### Level lane

Main entry:

```text
lowering/open_vector_pipeline.py
lowering/ir_to_abstract_physical.py
```

This lane handles dense Level expressions, periodic/multicycle state, vector operations, physical phases, and optional packing.

### Event lane

Main entry called by `compile_circuit()`:

```text
lowering/event_accumulator_physical.py
```

Supporting implementation files include the clocked payload/valid lowerer, Event state, derived clocks, stateful bridges, and occurrence reindexing.

Semantically, the Event lane realizes:

```text
Event = payload path + valid path
```

and aligns payload/valid to consumers and boundaries. Stateful bridges select a common execution phase before interacting with memory. The current direct additive Event accumulator case fuses valid-gated Event payloads into the destination feedback cell instead of constructing an intermediate bridge.

The physical Event class hierarchy is implementation organization, not additional semantic layers. New maintenance work should prefer factoring shared realization helpers over adding another milestone-specific subclass.

## 5. Abstract Physical IR

Main file:

```text
ir/abstract_physical.py
```

This IR is Factorio-target-specific but leaves late physical resources unresolved. It contains exact combinator operations plus abstract signals, electrical nets, runtime-open vector nets, and allocation/conflict metadata.

Its purpose is to keep these choices available to one synthesis stage:

```text
concrete signal identities
net compatibility / merging
red vs green wiring
entity placement
wire-reach-safe routing
```

Keep an abstraction here only when synthesis actually consumes it or when it enforces a real Factorio compatibility invariant.

## 6. Physical synthesis and Layout

Main files:

```text
synthesis/physical.py
synthesis/open_vector.py
synthesis/placement.py
synthesis/layout.py
```

Physical synthesis resolves abstract signals/nets into a concrete `PhysicalCircuit` embedded in a `Layout`. It owns signal allocation, wire colors, placement, and wire reach.

A completed `Layout` is the physical contract handed to blueprint serialization.

### Progress observability

Long physical synthesis is observable through the optional callback on the canonical entry point:

```python
compile_circuit(source, progress=callback)
```

The callback receives `CompileProgress` records. Coarse events identify frontend, timing, lowering,
synthesis, placement, blueprint encoding, and completion. Bounded physical stages additionally report
`completed`/`total` values. Routing reports one update per physical connection and the cumulative relay
count; when a difficult connection enters the half-tile grid fallback, `routing-search` reports search
expansions in bounded batches.

Progress callbacks are observational only. They must not alter placement seeds, work budgets, or
compiler output. This makes the same event stream suitable for terminal progress bars, CI diagnostics,
and future deterministic optimization/fallback budgets.

## 7. Blueprint serialization

Main file:

```text
blueprint/layout_encode.py
```

Serialization converts a finished Layout to blueprint JSON/string. It does not choose semantic timing, signals, wire colors, or geometry.

### In-game entity annotation convention

Every serialized combinator receives a `player_description` for physical debugging. Blueprint serialization adds a uniform header and preserves any lowering/synthesis role text already stored in the concrete entity's `description` field:

```text
[FCC #<entity-number> | <kind>] <role>
```

Examples:

```text
[FCC #37 | arithmetic *] Mapped FreezeReg body_mask: input gate
[FCC #91 | decider !=] Mapped AccumulatorReg score: add active
[FCC #144 | selector random] ORACLE food_candidate: random signal every 1 tick(s)
[FCC #208 | constant] mapped periodic commit: +1
[FCC #615 | relay] safe folded red group 12 row 3 track 4
```

The header fields are serialization-level diagnostics:

- `#<entity-number>` is the final blueprint entity number and is the stable lookup key for wires and positions in that blueprint.
- `<kind>` is a concise concrete target classification: `arithmetic <operation>`, `decider <comparator>`, `selector <operation>`, `constant`, `marker`, or `relay`.
- `<role>` is optional free-form provenance supplied by lowering or routing. It should describe why the entity exists, not restate its configured arithmetic. Existing role strings are preserved verbatim after the header.

Annotation is observational only. It must not change Abstract Physical IR, physical timing, signal allocation, net grouping, placement, routing, or combinator counts. Entities without an existing role still receive the header, so an otherwise opaque combinator remains identifiable in-game. I/O annotation-only constants use kind `marker`; routing relay constants use kind `relay`.

## 8. Simulation and tests

Semantic/reference simulation defines the intended logical behavior; physical simulation validates lowering after accounting for output phase.

Important areas:

```text
simulate/semantic.py          Level reference behavior
simulate/events.py            Event schedules/reactions
simulate/clocked_events.py    clocked Event reference entry
simulate/physical.py          physical combinator simulation
```

Broad regressions:

- `tests/integration/test_multi_rate_event_ledger.py` — irregular multi-clock Event semantics vs physical lowering;
- `tests/integration/test_abstract_physical_pipeline.py` — semantic -> Abstract Physical -> Layout path;
- `tests/integration/test_layout_benchmark_examples.py` — sorting/WHT synthesis and blueprint stress;
- `tests/timing/` — causality, clock contracts, state timing.

Completed one-off in-game probes should be removed once their result is encoded as a semantic rule plus a durable regression test.

## 9. Maintenance rule of thumb

When adding a feature, put it at the earliest layer that owns its meaning:

```text
user syntax / validation       -> frontend
logical meaning                -> semantic IR
logical recurrence legality    -> causality
Factorio latency feasibility   -> timing
combinator realization         -> lowering
signal/wire/placement choices  -> synthesis
JSON/string encoding           -> blueprint
```

Avoid letting a later layer infer semantics that an earlier layer should have made explicit.
