# Development Conventions v0.6

## Scope

Compile symbolic Python circuit descriptions into Factorio combinator blueprints, with
Factorio-specific optimization aimed primarily at reducing combinator count while preserving timed
stream behavior.

## Source/frontend

- `Circuit` is the primary behavioral unit.
- Python itself is the elaboration/metaprogramming language.
- `Input`, `SignalsInput`, and state objects are temporal/source objects.
- `Expr` represents a derived scalar logical stream.
- overloaded scalar operators construct logical IR.
- runtime branching uses `condition.select(when_true, when_false)`.
- Python `if`, `for`, functions, collections, and other control structures operate at elaboration time.
- derived expressions expose no `.sample()` operation.
- the former `@circuit` AST frontend is retired.

## Timing/freshness

- logical invocations are indexed by game tick `t`;
- a base scalar input `x` denotes stream sample `X[t]`;
- `Circuit.tick(n)` / `tick_until(n)` advance the freshness cursor only;
- `x.sample()` at cursor `τ` denotes `X[t+τ]`;
- previously constructed expressions keep their original sample provenance;
- stateless physical latency and operand alignment are compiler-inferred;
- state `.value` observations carry the current freshness offset;
- physical implementation phase remains distinct from semantic freshness.

## State ordering

For v1, operations on one state object follow strict Python elaboration order. Reads and updates each
receive an internal order identity. This total order is intentionally stronger than the likely final
partial-order semantics, because it keeps the public model simple while preserving a migration path to
explicit update events later.

State update operands may have inferred physical latency. The compiler now solves a first strict-v1
state timing plan for the trusted vector registers. One compound transition receives an elastic
semantic commit offset constrained by surrounding state reads; a separate physical state phase absorbs
implementation latency where possible. Infeasible same-boundary post-update reads and reads splitting a
compound transition are compile-time errors.

## State components

```text
AccumulatorReg
    whole-vector additive memory
    one or more commutative add sources, each optionally enabled
    clear control

FreezeReg
    whole-vector replacement/hold memory
    set != 0 -> pass/track
    set == 0 -> freeze/hold
```

Both have working in-game prototypes. Their physical feedback circuits are implementation choices.

Whole-vector state observations may feed other state transitions directly. The state scheduler solves
the resulting register-phase difference constraints jointly; zero-weight feedback cycles are valid,
while positive-latency cycles are rejected by the current one-transition-per-tick model.

A whole-vector value denotes a concrete red-wire network. `.signal(...)` extracts a lane through an
isolating combinator before scalar arithmetic. This keeps scalar expressions from electrically merging
independent vector or feedback networks.

## Factorio substrate

- red and green are distinct circuit-wire networks;
- a network carries a sparse map of named signed-`i32` lanes;
- same-name contributions add;
- arithmetic/decider combinators have one-tick latency;
- `Each` is a major vectorization mechanism;
- selector-combinator support remains postponed;
- blueprint layout must respect finite circuit-wire reach.

## Optimization

Keep the logical DAG and state primitives recognizable for simplification, CSE, DCE, compatibility
partitioning, `Each` packing, phase alignment, state realization, and late signal allocation. Avoid
turning the latency of a convenient first lowering into a semantic requirement.
