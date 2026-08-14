# Development Conventions

## Scope

Compile symbolic Python circuit descriptions into Factorio combinator blueprints while preserving
logical stream behavior independently from physical timing.

## Source/frontend

- `Circuit` is the primary behavioral unit.
- Python is the elaboration/metaprogramming language.
- `Input`, `SignalsInput`, and state objects are source objects.
- `Expr` is a derived scalar logical stream; `SignalsExpr` is a runtime-open vector stream.
- overloaded operators construct logical IR.
- runtime branching uses `condition.select(when_true, when_false)`.
- Python control structures remain elaboration-time Python.
- derived expressions expose no `.sample()` because they already denote sampled streams.

## Logical time

- logical streams are indexed by logical step `k`, not by Factorio game tick;
- inputs and registers are observed uniformly with `.sample()`;
- `Circuit.step(n)` / `step_until(n)` advance the logical observation cursor;
- previously constructed expressions keep their original logical sample provenance;
- `Circuit.tick()` is reserved for future explicit physical scheduling and currently raises;
- `register.value` is compatibility-only; new code uses `register.sample()`.

## Physical timing and clock domains

- Factorio combinators add one physical game tick;
- stateless physical latency and operand alignment are compiler-inferred;
- each ordinary connected state component has an inferred logical clock-domain period `P`;
- a value with physical phase `phi` realizes logical step `k` at `phi + k*P`;
- feed-forward latency does not enlarge `P`; recurrences do;
- ordinary state dependencies union registers into one domain, even for one-way dependencies;
- independent state components may have different periods;
- a genuine zero-logical-distance positive-latency cycle remains illegal;
- state communication between genuinely different periods requires explicit future rate-crossing
  semantics rather than implicit same-index arithmetic.

For source logical offset `r`, target commit offset `c`, physical latency `L`, shared period `P`, and
register phases `phi`, the analyzer uses:

```text
phi_target >= phi_source + (r - c - 1) * P + L + 1
```

The smallest feasible positive integer `P` is chosen per domain.

## State ordering

Operations on one state object follow strict Python elaboration order. Reads and updates receive an
internal order identity. Reads before one compound transition observe the old state; post-transition
reads require a later logical step; a read cannot split one compound transition.

The compiler chooses semantic commit offsets, physical phases, and clock periods. Source code does not
name a register write's physical game tick.

## State components

```text
AccumulatorReg
    whole-vector additive memory
    one or more commutative add sources
    optional clear control

FreezeReg
    whole-vector replacement/hold memory
    set != 0 -> pass/track at logical boundary
    set == 0 -> hold
```

For `P>1`, lowering synthesizes a modulo-domain clock and gates state writes so intermediate physical
ticks hold state.

Higher structures such as queues/stacks should first be expressed using these general state primitives.

## Factorio substrate

- red and green are distinct circuit-wire networks;
- a network carries a sparse map of named signed-`i32` lanes;
- same-name contributions add;
- arithmetic/decider combinators have one-tick latency;
- `Each` is a major vectorization mechanism;
- selector combinators support current vector selection operations such as `max()`;
- blueprint layout must respect finite circuit-wire reach.

## Optimization

Keep logical streams and state primitives recognizable through simplification, CSE, DCE, target-level
packing/fusion, phase alignment, state realization, late signal allocation, net coalescing, placement,
and routing. Never turn the latency or clocking of one convenient lowering into a source-language
semantic requirement.

Physical synthesis owns concrete signal identities, red/green choices, placement, and reach-safe
routing. Prefer target-graph reductions before increasing placement/router complexity, and measure
changes on the parameterized sorting/WHT benchmarks plus representative stateful circuits.
