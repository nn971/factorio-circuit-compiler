# Experimental temporal materialization

This document describes an **opt-in research prototype**. It is intentionally disconnected from the
canonical compiler pipeline so it can be removed cleanly if the model proves unhelpful.

Production modules must not import from `factorio_circuit.experimental`.

## Motivation

The Snake census shows that phase-alignment hardware dominates the current realization:

```text
implementation entities = 5,657
phase-delay entities     = 4,960
```

The present lowerer commits early to exact physical phases and then represents slack by chains of
one-tick identity combinators. The experiment instead treats unused ticks as temporal don't-cares and
allows synthesis to insert temporary physical state even when the frontend semantics contains no
register at that point.

## Phase-free hypergraph

`factorio_circuit.experimental.temporal_hypergraph` defines a computation hypergraph whose values are
logical token families and whose hyperedges are logical operations. A value records its payload shape,
clock identity, and logical offset, but **no physical phase**. Each operation records per-input target
latencies rather than an absolute execution tick.

Semantic outputs and state-transition inputs are stored as unresolved observations. Therefore building
the graph does not choose state-capture phases or manufacture phase-alignment delays.

Inspect the current Snake workload with:

```bash
uv run python -m benchmarks.snake.temporal_experiment
uv run python -m benchmarks.snake.temporal_experiment --no-framebuffer
```

This runner stops after semantic normalization and hypergraph construction.

## Finite periodic oracle

For a fixed candidate period `P`, the experimental exact solver models each value with a bit mask over
phases `0 .. P-1`. Bit `t` means that the physical representation is guaranteed to equal the desired
logical token at phase `t`.

A continuous operation with output phase `q` is correct when every input is correct at the phase
required by that input's intrinsic latency. Correctness is propagated to a fixed point.

A synthetic scalar materializer may capture a value at a phase where that token is correct. After the
configured capture latency, it holds the same logical token through the end of the period. This is an
identity transformation on logical tokens; it changes only physical validity.

The reference search performs breadth-first search over materialization choices. Every scalar hold has
unit cost, so the first solution covering every required demand is an exact minimum-cardinality plan
for this restricted model.

The oracle deliberately omits:

- vector-bank packing;
- wrap-around demand windows;
- Event clocks;
- concrete Factorio memory implementation;
- clock-period search;
- large-graph performance.

Its purpose is to validate examples such as:

1. an early value needed much later requires one hold instead of a long delay chain;
2. a naturally stable value requires no hold;
3. when `z=f(x,y)` is needed late, holding `z` can beat holding both inputs;
4. when one input fans out to several late computations, one shared upstream hold can beat holding
   each derived result.

Those examples are covered by `tests/experimental/test_temporal_hypergraph.py`.

## Snake-scale delay-reuse projection

The exact oracle is intentionally too expensive for the full Snake graph. As an intermediate
measurement, `factorio_circuit.experimental.delay_reuse` analyzes the **current eager Abstract Physical
IR only as a baseline**. It groups maximal scalar/vector phase-delay DAGs that carry the same logical
Level token forward in time.

The projection then applies two conservative rules:

1. if the root net is structurally constant, every delay in that component can be removed directly;
2. otherwise, if the component's longest delay path is shorter than the inferred clock period, one
   synthetic capture/hold at the component root can replace the whole component.

This is intentionally not claimed to be optimal. The phase-free hypergraph may later prove a broader
natural validity interval, share one hold across several baseline components, or pack many values into
one Factorio vector bank. Therefore the projection is an **upper bound on synthetic hold count** and a
conservative lower bound on delay elimination.

Run it with:

```bash
uv run python -m benchmarks.snake.delay_reuse_experiment --no-framebuffer
uv run python -m benchmarks.snake.delay_reuse_experiment
```

The runner verifies that its eager-delay count matches the Abstract Physical census, then prints:

- scalar/vector eager delay count;
- number and depth distribution of maximal delay components;
- components removable for free because their root is invariant;
- components replaceable by one temporal hold;
- components not yet covered by this simple rule;
- projected remaining delay count;
- a rough implementation count assuming one entity per synthetic hold.

That last count is **not yet an executable Factorio circuit**. Capture-clock hardware, a real hold
primitive, and vector-bank sharing are deliberately excluded until the temporal model is trusted.

## Intended next steps

If the oracle behaves correctly, the next mathematical step is to identify the restricted scalar case
that can be reduced to a periodic minimum-cut problem, then compare that fast solver against this exact
oracle on randomly generated small hypergraphs.

After the scalar formulation is trusted, Factorio vector storage can be added as grouped/rectangle
materialization: one physical bank may hold several compatible logical values captured at the same
phase. That changes additive cut cost into a grouped/subadditive cost and is likely to require a
separate packing or column-generation layer.

Only after these analyses are validated should any experimental materializer be lowered into real
Factorio combinators.

## Rollback

The experiment is isolated to newly added paths:

```text
src/factorio_circuit/experimental/
tests/experimental/test_temporal_hypergraph.py
tests/experimental/test_delay_reuse.py
benchmarks/snake/temporal_experiment.py
benchmarks/snake/delay_reuse_experiment.py
docs/experimental-temporal-materialization.md
```

No canonical compiler, lowering, synthesis, or benchmark baseline file depends on them. Removing those
paths restores the exact previous production behavior. The pre-experiment branch anchor is
`146bd0ead93057b0c8a1d0eb104c0eb58ae138e1`.
