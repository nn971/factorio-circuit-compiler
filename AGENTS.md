# AGENTS.md

## Project purpose

Build an experimental compiler from a symbolic Python circuit EDSL to optimized Factorio 2.x
circuit-network blueprints. Prioritize correct timed behavior and Factorio-specific combinator-count
reduction.

## Architectural invariants

1. Keep the pipeline small: symbolic Python elaboration -> logical circuit -> abstract physical IR -> physical synthesis/Layout -> blueprint serialization.
2. Python outside symbolic operations is ordinary elaboration/metaprogramming Python.
3. `Circuit`, source objects, `Expr`, and built-in state objects are the public frontend; do not revive
   AST parsing as the primary language model.
4. Symbolic expressions denote logical streams; physical combinator phase is inferred later.
5. Raw input sources may be sampled at explicit freshness offsets; derived expressions have opaque
   execution timing and are not sampleable sources.
6. Keep useful state primitives recognizable until Factorio-specific realization.
7. Compiler-chosen signal identities, red/green wiring, and entity coordinates are late resources;
   user-selected target signals remain fixed semantic inputs to physical synthesis.
8. Validate physical behavior with tick-level simulation and in-game blueprints.
9. Treat naïve physical latency as a property of that realization rather than an intrinsic semantic
   lower bound.

## Factorio target conventions

- red and green are distinct wire networks;
- each network carries a sparse map `SignalId -> signed i32`;
- same-name contributions add;
- arithmetic/decider combinators have one-tick latency;
- `Each` is a first-class vectorization/state mechanism;
- initial backend focuses on constant, arithmetic, and decider combinators;
- selector combinator remains postponed;
- blueprint generation must respect finite wire reach.

## Current source/timing model

A `Circuit` describes deterministic logical streams indexed by invocation tick `t`.

```python
c = Circuit("example")
x = c.input("x")
y = (x + 1) * 2
c.output("y", y)
```

Operators construct symbolic IR. Python control flow is elaboration-time control flow; use
`condition.select(a, b)` for runtime dataflow branching.

`c.tick(n)` / `c.tick_until(n)` advance a freshness cursor only. They do not mutate expressions that
were already constructed.

```python
x0 = x
c.tick(3)
x3 = x.sample()
```

means `x0[t] = X[t]` and `x3[t] = X[t+3]`.

State `.value` observations also carry the current freshness offset.

## State ordering

For v1, accesses to one state object are semantically ordered by Python elaboration order. The user
therefore captures an earlier state version by reading it before issuing a later update.

Internally every state read/write carries an explicit integer order identity. Preserve this metadata:
it is the migration path to a future partial-order/update-event frontend such as `read(before=u)` or
`read(after=u)`.

State writes remain elastic with respect to physical combinator timing. The current state timing
analyzer assigns one semantic commit offset to each trusted register's compound transition, derives its
legal interval from surrounding ordered reads, and separately solves the physical state/input phases.
Same-boundary post-update reads and reads splitting a compound transition are compile-time errors.
Preserve this semantic/physical separation when extending the scheduler.

## Current state components

- `AccumulatorReg`: whole-vector additive memory with clear.
- `FreezeReg`: whole-vector pass/freeze memory; `set!=0` tracks input and `set==0` holds.

The malfunctioning generic scalar `Reg` remains removed. `Delay`, interleaving, queues/stacks/heaps,
and processor-like synthesis remain postponed.

## Abstract physical IR boundary

`AbstractPhysicalCircuit` is the target-specific pre-layout representation. It contains exact target
combinators, abstract signal variables, abstract electrical nets, and compatibility/conflict metadata.
Signals do not belong to one net. Concrete signal identities, red/green assignment, net merging,
coordinates, and relay placement remain unresolved.

Physical synthesis owns those late choices jointly and returns the final `Layout`. Blueprint generation
only serializes and encodes that layout. Read `docs/abstract-physical-ir.md` before changing this
boundary.

## Canonical physical backend

`compile_circuit(...)` runs through
`AbstractPhysicalCircuit -> physical synthesis -> Layout -> blueprint serialization`. It supports
scalar and whole-vector I/O, fresh scalar/vector samples, vector constants, direct fixed-lane
`.signal(...)` views, scalar logic, conservative `Each` packing, `AccumulatorReg`, and `FreezeReg`.
Runtime-open vector nets and fixed target signals are explicit in the abstract IR. Register
vector/control separation is expressed with `NetConflict`, not concrete wire colors. Physical synthesis
reserves fixed signals, derives safe red/green constraints, coalesces compatible shared-connector nets,
and reuses concrete virtual signals across electrically disjoint physical groups. Switchable Fibonacci
is the coupled-state regression.

`compile_abstract_circuit(...)` is only a compatibility alias. The previous direct-concrete backend is
available from `factorio_circuit.compiler_legacy` solely as a parity/debugging oracle.

## Immediate next route

1. keep the current deterministic row placement and reach-safe routing unless correctness requires a
   backend change;
2. retain tick-level simulation plus structural checks for dead/orphan blueprint artifacts;
3. keep a small set of legacy parity tests where the old realization is still a useful oracle;
4. return to semantic design/features only after this cleanup is green under pytest, ruff, and mypy.

## Representative timing tests

Keep reusable timing circuits in `tests/support/circuits.py`. Current canonical cases are the delayed
accumulator window and the parameterized n-tick pulse generator. Prefer semantic-vs-physical stream
comparisons over blueprint-shape-only assertions when the simulator supports the feature.

## Testing

With fish + uv:

```fish
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```
