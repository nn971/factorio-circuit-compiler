# Physical layout

This document records the current physical-layout contract. Historical tuning tables and branch experiment ledgers belong in Git/PR history; accepted benchmark measurements belong with the benchmark.

## Ownership boundary

Physical synthesis receives a target-specific `AbstractPhysicalCircuit` and produces a concrete `PhysicalCircuit` embedded in a `Layout`. It owns:

- concrete signal allocation;
- red/green electrical realization;
- combinator placement;
- relay placement;
- wire-reach-safe routing;
- final exact entity/wire geometry handed to blueprint serialization.

Blueprint serialization does not repair placement, routing, or timing.

## Layout strategies

The normal compact path uses annealed placement/routing. It may jointly move implementation combinators and relay constant combinators, but it must start from and return a feasible routed state.

Two constructive fallbacks remain useful:

- `safe-crossbar` is the simple search-free correctness/rollback reference;
- `safe-folded-crossbar` mechanically folds that construction into a bounded geometry.

Their detailed constructions remain in `safe-crossbar-layout.md` and `safe-folded-crossbar-layout.md`.

An already-routed `Layout` can also be optimized independently through `synthesis/layout_optimizer.py`. The public problem describes the exact input layout, legal unit/wide placement sites, reserved regions, fixed coordinates, and conservative wire-reach limit. A zero proposal budget is an exact pass-through.

## Feasible-first invariants

- Optimization starts from an explicitly reach-safe routed topology and may not return an invalid one.
- Implementation combinators and relay constant combinators share the same corridor-aware legal workspace. Reserved corridors exclude both classes.
- Optimizer state is the source of truth for geometry. Relay coordinates in a returned routing plan must be materialized from the final optimizer state.
- Final validation checks the exact coordinates/connectors/wires that will be serialized, not a simplified proxy.
- Explicit user anchors remain fixed. Automatic public I/O anchors may be recomputed when the occupied envelope changes.
- Local proposal evaluation should stay local; expensive topology replacement belongs at coarse transactional boundaries rather than the annealing hot loop.
- A failed sequential relay allocation may be cross-net congestion caused by greedy ordering. It is not by itself a proof of global infeasibility.

## Objective

For routed layouts, the durable comparison order is lexicographic:

```text
relay count
occupied bounding-box area
total routed wire length
```

Feasibility dominates all three. An optimizer must retain the input as a valid best-known fallback and return no worse than that fallback under the advertised objective.

## Acceptance

Small layout changes should have focused structural tests. Changes to routing or lowering should additionally use physical simulation where practical.

Heavyweight Snake layout work is accepted only after the exact generated blueprint passes the benchmark's in-game procedure. Routine CI is necessary but does not substitute for target-level acceptance of large physical-layout changes.

Use `benchmarks/snake/baselines.json` for accepted measurements and PR history for rejected/tuning experiments.
