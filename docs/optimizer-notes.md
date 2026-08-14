# Optimizer Notes

This file is exploratory. Hypotheses become architecture only after benchmark or in-game evidence.

## Implemented baseline

The compiler currently performs:

- semantic constant folding/algebraic simplification, common-subexpression elimination, and dead-code
  elimination for the scalar semantic path;
- phase-aware scalar lowering and automatic operand alignment;
- runtime-open vector lowering;
- conservative `Each` packing, including generic pairwise arithmetic batches;
- shared-predicate / multi-output decider fusion;
- state timing with inferred logical clock-domain periods;
- Factorio-native `AccumulatorReg` and `FreezeReg` realization;
- late abstract-signal allocation and compatible electrical-net coalescing;
- net-aware placement with deterministic retries, reserved access/power corridors, and reach-safe
  relay routing;
- semantic reference simulation and tick-level physical simulation.

Representative stress circuits are the parameterized sorting network and Walsh-Hadamard transform,
plus the FIFO/stack and autonomous-market controller for stateful timing/layout behavior.

## Optimization boundary

Keep semantic streams and useful state components recognizable until a Factorio-native realization is
selected. The abstract physical IR already fixes target combinator behavior while leaving concrete
signal identities, wire colors, compatible net merging, and placement to physical synthesis.

Physical optimization should therefore prefer transformations that improve the target graph before
adding increasingly complicated geometry heuristics. Useful examples include:

- better combinator selection and algebraic target rewrites;
- broader but proven-safe `Each` packing;
- predicate/result sharing;
- red/green-aware realization choices expressed through abstract compatibility metadata;
- improved state realization when a shorter feedback structure is semantically equivalent;
- signal/net reuse that removes physical graph structure rather than merely renaming lanes.

## Placement and routing

The current default placer is net-aware rather than row-based. It treats synthesized electrical groups
as hyperedges, optimizes a reach/connectivity/MST-style objective, reserves regular block corridors,
and retries deterministic placement basins when routing fails. Row placement remains a compatibility
and debugging strategy.

The autonomous-market controller exposed a remaining gap: a placement with a good approximate net
objective can still be awkward for the concrete point-to-point relay router. Future geometry work may
include:

- objectives based on actual routed congestion rather than only idealized net metrics;
- feedback from failed routes into subsequent placement attempts;
- joint routing of one electrical group so relay infrastructure can be shared safely;
- explicit power-entity emission at the already-reserved substation corridor crossings.

Do these after target-graph improvements and measure them on the benchmark circuits.

## Timing/state directions

The current periodic state-domain realization is correct for level-like inputs sampled at logical
boundaries. The most important semantic extension under consideration is event-triggered activation:
interpret inferred `P` as a minimum initiation interval and snapshot external inputs when an activation
is accepted. See `docs/timing-open-problems.md`.

Other postponed ideas include explicit clock-domain crossing, temporal resource sharing, processor /
interpreter synthesis, additional state packing, and explicit physical-tick constraints. They should
return only when a representative circuit demonstrates a concrete need.

## Benchmark discipline

Use `benchmarks/README.md` and the parameterized examples as the measurement baseline. Compare at least
combinator count, latency/output phases, state periods, placement/routing metrics, footprint, and
synthesis runtime where relevant. Exact metric assertions should be reserved for intentional regression
guards; exploratory heuristics should remain easy to replace.
