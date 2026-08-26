# Compiler roadmap

This is the current forward-looking roadmap for the compiler. It complements the subsystem contracts in this directory: those documents describe what the compiler means today, while this file records the next durable engineering directions and their acceptance criteria.

Detailed tuning logs, rejected experiments, and branch handoffs belong in Git/PR history. Accepted benchmark measurements belong with their benchmark.

## Guiding principles

1. **Feasibility before optimization.** Physical optimization begins from a validated artifact and retains a validated best-known fallback.
2. **Measure before tuning.** Layout, mapping, and temporal changes need representative benchmarks and observable work counters before heuristic tuning is treated as progress.
3. **Preserve explicit contracts across layers.** Semantic timing, physical interfaces, placement constraints, and serialized geometry each need one authoritative representation.
4. **Prefer differential verification.** Whenever two independent realizations exist, compare them automatically rather than relying only on hand-written expected outputs.
5. **Keep heavyweight acceptance opt-in.** Routine CI should stay fast; large layout and in-game acceptance runs remain separate but reproducible.

## Milestone A — Layout reliability corpus

**Status: complete.**

**Goal:** make layout failure modes reproducible before further annealer tuning.

The structural corpus covers topology and geometry dimensions rather than only application examples:

- sparse independent entities;
- independent long relay chains;
- high-degree shared nets and stars;
- clustered graphs with sparse cut nets;
- meshes and bus-like shared connectivity;
- mixed 1x1 and 2x1 implementation footprints;
- fixed anchors;
- anchor-heavy perimeter interfaces;
- reserved/forbidden regions and narrow routing corridors;
- valid but deliberately poor initial embeddings;
- nearly optimal embeddings;
- an opt-in 1,200-object sparse scale case.

Stochastic benchmark runs support consecutive multi-seed sweeps and report distributions rather than one favorable seed. For every run, the public fail-safe property is:

```text
valid input
    -> finite optimization budget
    -> valid output
    -> output objective <= input objective
```

The public physical objective remains lexicographic:

```text
relay count
occupied bounding-box area
total routed wire length
```

### A acceptance

- Each structural family has a deterministic constructor or seedable generator.
- Routine structural fixtures validate before optimization.
- Optimizer runs validate after optimization.
- Multi-seed runs retain the input as the worst permitted result.
- The 1k+ scale fixture has an explicit opt-in validation path rather than burdening routine CI.
- Any discovered failure should be reduced to a small regression case when practical.

The corpus lives in `benchmarks/layout_optimizer_corpus.py` and
`benchmarks/layout_optimizer_topology_corpus.py`; usage and scale-tier instructions are in
`benchmarks/README.md`.

## Milestone B — Annealer observability

**Status: complete.**

**Goal:** expose where optimization work is spent and why proposals fail.

The opt-in `OptimizationStats` surface records:

- proposals attempted and accepted moves;
- Metropolis, geometry/occupancy, and wire-reach rejections;
- implementation moves vs relay moves;
- swaps;
- relay simplification classified as isolated deletion, leaf deletion, or degree-two bypass;
- topology rebuild attempts/successes;
- routing search calls/failures and deterministic priority-queue work;
- best-objective history by epoch;
- epochs since last improvement.

The stats are observational only. The production annealer remains authoritative, and regression tests require a fixed seed and work budget to produce the same optimized artifact through the observed path.

### B acceptance

- Benchmark output explains dominant rejection/work categories.
- Stats are stable enough for regression analysis without turning heuristic details into semantic contracts.
- A fixed seed and proposal budget produce the same optimized artifact with stats collection enabled.
- Routing-work and relay-simplification counters have accounting regressions rather than relying on elapsed wall-clock time.

The opt-in report is `benchmarks/layout_optimizer_observability.py`.

## Milestone C — Annealing v2

**Status: current.**

**Goal:** improve quality and speed while preserving feasible-first behavior.

Prioritized experiments:

1. adaptive coarse retopology triggered by stagnation or congestion rather than only fixed budget fractions;
2. transactional compound moves such as terminal+adjacent-relay moves and short relay-chain translations;
3. adaptive proposal pools based on congestion, envelope outliers, and objective stagnation;
4. targeted local repair around hard anchors and routing bottlenecks;
5. only after measurement, lower-level performance work in occupancy, routing indexes, and proposal evaluation.

### C experiment log

- **Adaptive coarse retopology: rejected.** A conservative stagnation/wire-reach trigger was measured against the fixed 25/50/75% schedule on four representative corpus families, three identical seeds each, at 4,096 proposals. All 12 paired runs produced the same physical objective, while adaptive runs performed four extra rebuilds each. Routing cases increased deterministic search work (`63 -> 147` queue pops on the fixed-span case and `21 -> 49` in the narrow corridor), with roughly 2–9% runtime overhead. The experiment was removed from production rather than threshold-tuned without a quality signal.
- **Terminal + adjacent-relay compound rescue: current experiment.** When an implementation move would otherwise fail only on wire reach, translate the directly offending movable relays by the same displacement as one transaction. Fixed relays, illegal sites, stationary collisions, moving-object collisions, or a broken far-side hop reject the rescue. The proposal uses the original random target and Metropolis decision, so the experiment adds no new RNG path.

### C acceptance

- Every accepted optimization is benchmarked across multiple seeds.
- Improvements are reported separately for relay count, area, wire length, and work/runtime.
- Recoverable search failures return the best validated candidate; invariant violations remain visible as bugs.

## Milestone D — Physical ABI completion and placement integration

**Goal:** finish the reusable physical-module boundary and make layout consume its geometry directly.

### Already landed

Current main already provides important pieces of this milestone:

- typed exact-overlap `AnchoredBlueprint` terminals;
- `DeviceProtocol` / `ExternalDeviceBlueprint` adaptation;
- `ConstrainedComponent` footprints, boundary slots, and ordered seams;
- rigid seam composition derived from matching public lanes;
- post-synthesis compiled-module adaptation through stable anchors.

These contracts are documented in `device-anchoring.md` and `component-seam-abi.md` and should remain the basis for further ABI work.

### Remaining physical-placement work

Prioritize the gaps the current ABI deliberately leaves open:

- robust pre-placement public-port pinning for distant explicit anchors;
- hard component keepouts consumed by placement and joint annealing;
- automatic reserved adapter regions around ABI seams;
- prototype-aware footprint/collision treatment where centre-only confinement is insufficient;
- rigid multi-entity macro placement inside the same optimizer state as ordinary combinators and relays;
- through-bus/tap/contribution seam roles when a concrete device requires them.

Avoid extending the ABI with speculative roles until a real device or benchmark exercises them.

### D acceptance

- A rigid multi-entity component can participate in final placement/routing without losing its internal geometry.
- Hard component regions are respected by the same feasibility checks used by relays and implementation entities.
- Distant explicit public anchors receive validated relay workspace rather than relying on post-hoc repair.

## Milestone E — Oracle/device/layout unification

**Goal:** let oracle providers and reusable external components participate in one physical composition story.

The oracle provider insertion point already runs before signal allocation, wire-color assignment, placement, and routing. Extend the boundary so providers can materialize reusable physical components or ABI seams where appropriate while retaining existing per-entity free/anchored placement for simple providers.

Standalone device generation should remain useful for manual probes; compiler integration should reuse the same typed boundary rather than inventing a second device protocol.

### E acceptance

- One compiler run can jointly realize ordinary logic, freely placeable provider helpers, anchored sensors, and rigid device components.
- The final serialized layout validates exact pins, anchors, footprints, and wires.

## Milestone F — Useful peripheral set

**Goal:** expand devices in an order that exercises new compiler capabilities.

Current main already contains the movement detector, packed-RGB lamp screen, and reusable assembler device. Suggested next additions are:

1. programmable speaker output;
2. roboport/logistic-stock vector reader;
3. belt/inserter pulse readers for Event integration;
4. richer machine/train interfaces after anchored macro placement is robust.

Prefer devices that double as integration benchmarks for the ABI, Event semantics, or anchored layout.

## Milestone G — Differential compiler fuzzing

**Goal:** compare reference semantics with compiled physical simulation automatically.

Generate random programs inside the currently supported semantic subset, including combinations of:

- scalar/vector arithmetic;
- periodic state;
- Event state;
- `sample_on`;
- `gate_clock`;
- `event_merge`;
- `sum_into`;
- `hold_into`;
- output materialization policies.

For a fixed input/oracle trace, compare semantic execution against physical simulation. Shrink disagreements into minimal regression tests.

### G acceptance

- Seeded failures are reproducible.
- The shrinker can reduce common expression/state/clock mismatches.
- Unsupported language shapes are filtered or expected to reject explicitly.

## Milestone H — Multilevel/global physical optimization

**Goal:** give large circuits a better global seed before local joint annealing.

Explore coarsening the physical-net hypergraph into clusters, placing the coarse graph, expanding it, then using the existing feasible joint annealer for local refinement. The current annealer remains the correctness-preserving local optimizer rather than being replaced outright.

### H acceptance

- Large structural corpus cases improve in quality or work compared with flat annealing at equal validation guarantees.

## Milestone I — Independent blueprint-level verifier

**Goal:** verify the exact serialized artifact independently of synthesis internals.

Reconstruct from the final blueprint/layout:

- entity footprints and overlaps;
- connector identities;
- red/green connectivity;
- wire reach;
- public ports;
- ABI anchors, seams, and component regions;
- intended electrical-net equivalence where practical.

This verifier should share as little mutable synthesis state as practical so that it can catch serialization/materialization mistakes rather than merely repeat them.

## Implementation order

The immediate sequence is:

```text
A. layout reliability corpus [complete]
    -> B. annealer observability [complete]
    -> C. annealing v2 [current]
```

Milestone D can proceed in parallel, but new layout constraints should be introduced only with structural benchmark coverage. Then:

```text
D. physical ABI placement integration
    -> E. oracle/device/layout unification
    -> F. additional useful peripherals
```

Milestone G should begin as soon as a small useful random-program generator exists and then grow continuously. H and I become especially valuable once larger devices and benchmark applications exercise the full physical pipeline.

## Current step

Evaluate **terminal + adjacent-relay compound rescue** as the second Milestone C experiment. First preserve the production/observed decision equivalence and prove local transactional safety. Then measure compound attempts/accepts and compare compound-enabled runs against the same seeds with rescue disabled. Keep the change only if the corpus shows a quality or work benefit; otherwise remove it and advance to the next proposal-family experiment.
