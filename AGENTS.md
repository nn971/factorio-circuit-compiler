# AGENTS.md

## Project purpose

Build an experimental compiler from a symbolic Python circuit EDSL to optimized Factorio 2.x
circuit-network blueprints. Prioritize correct timed behavior and Factorio-specific combinator-count
reduction.

## Architectural invariants

1. Keep the pipeline small: symbolic Python elaboration -> logical circuit -> physical Factorio circuit -> blueprint.
2. Python outside symbolic operations is ordinary elaboration/metaprogramming Python.
3. `Circuit`, source objects, `Expr`, and built-in state objects are the public frontend; do not revive
   AST parsing as the primary language model.
4. Symbolic expressions denote logical streams; physical combinator phase is inferred later.
5. Raw input sources may be sampled at explicit freshness offsets; derived expressions have opaque
   execution timing and are not sampleable sources.
6. Keep useful state primitives recognizable until Factorio-specific realization.
7. Concrete signal identities, red/green wiring, and entity coordinates are late resources.
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

## Immediate next route

1. add explicit semantic write-time anchoring for timer-like state updates on top of the existing
   commit-offset solver;
2. drive that API with a reusable stateful timer/pulse representative test;
3. define startup/warm-up semantics for future-sampled sources entering feedback state;
4. only then investigate a public partial-order/update-event API;
5. study commutative accumulator update merging;
6. add further state types only from concrete use cases.

Read `docs/state-design.md` before changing state semantics.

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
