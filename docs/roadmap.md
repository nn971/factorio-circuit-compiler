# Compiler roadmap

This is the current forward-looking roadmap for the compiler. It complements the subsystem contracts in this directory: those documents describe what the compiler means today, while this file records the next durable engineering directions and their acceptance criteria.

Detailed tuning logs, rejected experiments, and branch handoffs belong in Git/PR history. Accepted benchmark measurements belong with their benchmark.

## Guiding principles

1. **Feasibility before optimization.** Physical optimization begins from a validated artifact and retains a validated best-known fallback.
2. **Measure before tuning.** Layout, mapping, and temporal changes need representative benchmarks and observable work counters before heuristic tuning is treated as progress.
3. **Preserve explicit contracts across layers.** Semantic timing, physical interfaces, placement constraints, and serialized geometry each need one authoritative representation.
4. **Prefer differential verification.** Whenever two independent realizations exist, compare them automatically rather than relying only on hand-written expected outputs.
5. **Keep heavyweight acceptance opt-in.** Routine CI should stay fast; large layout and in-game acceptance runs remain separate but reproducible.
6. **Treat application thresholds as evidence, not theology.** A numerical benchmark target can motivate engineering work, but it should not remain a hard milestone gate after a general solution is demonstrably practical, correct, and satisfactory in the actual game.

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

The corpus lives in `benchmarks/layout_optimizer_corpus.py` and `benchmarks/layout_optimizer_topology_corpus.py`; usage and scale-tier instructions are in `benchmarks/README.md`.

## Milestone B — Annealer observability

**Status: complete.**

**Goal:** expose where optimization work is spent and why proposals fail.

The opt-in `OptimizationStats` surface records proposals, accepted moves, rejection classes, implementation/relay motion, swaps, relay simplification, topology rebuilds, routing search work, best-objective history, and stagnation. The stats are observational only; the production optimizer remains authoritative.

### B acceptance

- Benchmark output explains dominant rejection/work categories.
- Stats are stable enough for regression analysis without turning heuristic details into semantic contracts.
- A fixed seed and proposal budget produce the same optimized artifact with stats collection enabled.
- Routing-work and relay-simplification counters have accounting regressions rather than relying on elapsed wall-clock time.

The opt-in report is `benchmarks/layout_optimizer_observability.py`.

## Milestone C — Annealing v2 / multilevel physical placement

**Status: complete.**

**Goal:** build a general-purpose physical optimizer that can start from a failproof validated layout, preserve feasible-first behavior, and produce a compact, relay-efficient, exact-valid blueprint on Snake-scale circuits in bounded work.

Milestone C began with a flat joint annealer and ended as a multilevel placement-and-rerouting pipeline. The key lesson was that Snake-scale geometry cannot be recovered efficiently by moving hundreds of individual combinators through an already-routed sparse scaffold. The successful design separates global implementation geometry from final relay routing.

### What landed

The retained path combines the following circuit-generic mechanisms:

1. **Fail-safe routed-layout optimization boundary.** Optimization consumes an already-valid `Layout`, carries fixed-position and lattice constraints explicitly, exact-validates accepted results, and retains the valid input/best-known artifact as fallback.
2. **Observability and exact best tracking.** Proposal/rejection/routing work is measurable, transient exact lexicographic improvements are retained, and stable `RoutedWire` hashing preserves deterministic seeded behavior without changing the historical annealing trajectory.
3. **C2 relay-blind hypergraph coarsening.** Logical red/green electrical hypernets are reconstructed without consulting the current relay scaffold or physical distances. Deterministic heavy-edge matching reduced the 613 Snake implementation/marker objects through `613 -> 320 -> 173 -> 98 -> 58 -> 38 -> 27` macros while keeping fixed public markers singleton.
4. **C3 genuine coarse macro contraction.** The coarsest macros remain real placement objects rather than immediately expanding back to hundreds of combinators. Their footprints derive from member implementation area plus bounded packing slack, fixed anchors remain exact, and deterministic legalization produces a compact coarse geometry.
5. **C4 coarse macro annealing.** Macro translations, affinity-directed migration, swaps, small compound moves, and bounded zoom pressure optimize occupied macro area, projected logical-hypernet HPWL, and a congestion estimate before expensive fine routing exists.
6. **C6 hierarchical uncoarsening.** The optimized hierarchy is expanded level by level. Children subdivide their actual optimized parent region, packing slack falls toward the real singleton footprints, and each level may receive transactional coarse refinement. This avoids the severe scattering seen when hundreds of singleton objects are globally legalized at once.
7. **C5 transactional fresh rerouting.** At the final implementation geometry, the old failproof relay scaffold is discarded. Routing starts fresh from zero relays, rebuilds the connector-aware logical nets, simplifies the resulting relay topology to a fixed point, and exact-validates the physical layout. Failure returns the original validated layout unchanged.

### Structural regression evidence

The frozen pre-C baseline remains `a70df723768a6ba099ffd43017bdcb0291011c8f`.

The completed structural full gate contains 101 baseline/current pairs across the structural families, proposal budgets 256 / 1,024 / 4,096 / 16,384, and the 1,200-object scale fixture. It produced **6 better / 95 equal / 0 worse** public lexicographic outcomes. All six improvements were wire-length improvements at unchanged relay count and occupied area; measured search-work counters were unchanged and the overall median runtime ratio was **1.031x**.

This evidence remains useful as a regression guard, but the decisive C result is the large application artifact below.

### Accepted Snake result

The early flat application check was correct in game but only about **4.0% physical occupancy** after 4,096 proposals, with 2,482 relays and a 91,805-tile bounding box. Increasing the flat proposal count had strong diminishing returns. That failure motivated the multilevel architecture above.

The accepted seed-0 C3 -> C4 -> C6 -> fresh C5 pipeline produces the current Snake physical artifact with:

```text
internal implementation combinators     602
public input/output markers               11
relay combinators                         174
placed physical entities                  787
implementation footprint area           1204 tiles²
exact routed bounding-box area           2116 tiles²
physical occupancy                     65.12%
routed wire length                    2829.56
implementation / relay ratio             3.46
C6 hierarchical uncoarsening            93.84 s
fresh C5 reroute                         12.19 s
measured end-to-end pipeline            155.98 s
```

The final artifact is exact-valid, uses only circuit-generic placement/routing mechanisms, preserves the fixed public markers, and was imported and exercised in Factorio. The application behaved correctly and the resulting physical layout was judged satisfactory in game.

### C acceptance decision

The previous **strictly greater than 80% occupancy** rule was a deliberately aggressive provisional target used to force the project away from the ~4% flat result. It succeeded at revealing the need for multilevel placement, but it is no longer a hard milestone gate.

Milestone C is accepted at the validated **65.12%** Snake result because the substantive goals are now met:

- a complete failproof artifact is always available as fallback;
- the optimizer is circuit-generic and contains no Snake-specific placement/scoring rule;
- global geometry is solved at an appropriate multilevel scale rather than by benchmark-specific squeezing;
- the final implementation placement is freshly rerouted and exact-validated;
- relay overhead is dramatically lower than the original failproof/flat layouts;
- work is explicitly bounded and measured;
- the serialized blueprint has been tested successfully in the real game and is physically compact enough to be practical and visually satisfactory.

Physical occupancy remains an important reported quality metric. **80% is retained as a stretch optimization target, not a correctness or roadmap blocker.** Fine routed compaction, push-and-drag cluster motion, and further global-density work belong to later optional physical-optimization work unless a future application demonstrates a concrete need.

The detailed historical acceptance and results documents retain the benchmark definitions and evidence.

## Milestone D — Physical ABI completion and placement integration

**Status: complete.**

**Goal:** finish the reusable physical-module boundary and make layout consume component geometry and interface constraints directly.

### What landed

Milestone D has a complete generic implementation path:

1. **D1 — authoritative component geometry constraints.** `RigidComponentConstraint` makes owned footprints, keepouts, reserved adapter regions, legal poses, and named boundary access points part of the physical optimization problem. Component members are validated against the same exact layout boundary used by placement and routing.
2. **D2 — rigid macro participation.** Multi-entity components can translate as one rigid body through a transactional move that discards the old relay scaffold, reroutes from scratch, exact-validates the candidate, and returns the original problem unchanged on failure. A bounded coordinate-descent driver can evaluate finite declared origins without breaking rigid geometry.
3. **D3 — anchored interface routing.** Named public input/output ports can be pinned to distant exact coordinates before final routing. The router reserves a deterministic same-phase relay chain from the public marker to a declared component access point, fixes those objects, fresh-routes the remaining nets, and exact-validates the result.
4. **D4 — real application benchmark.** The existing 25-entity `AssemblerDevice` is imported into physical synthesis with explicit caller-supplied prototype half-extents and connector shapes. Opaque device entities preserve their raw Factorio machine/chest/inserter payloads while participating in physical connectivity. The benchmark rigidly translates the complete device by 24 tiles, preserves an owned footprint plus keepout and adapter region, routes distant `recipe` and `ingredients` public interfaces through D3, and serializes the mixed compiler/device layout back to a Factorio blueprint.

The D4 importer deliberately does **not** introduce a hidden vanilla/mod prototype database. A reusable blueprint supplies the physical facts required for its prototypes, so modded devices can use the same generic path.

The imported assembler contains one legitimate 8.322-tile device-internal circuit span. Because it is an already-materialized Factorio blueprint, D4 validates/reroutes that component against the existing 9-tile vanilla combinator reach; the compiler's conservative 7-tile constructive-routing default remains unchanged.

### D4 automated evidence

The end-to-end D4 acceptance scenario passed with the ordinary suite enabled:

```text
567 passed, 33 skipped, 15 deselected
```

It checks:

- all 25 source device entities are rigid members;
- the 3x3 assembler uses its explicit `(1.5, 1.5)` half-extent rather than combinator geometry;
- every device member moves by exactly the same +24-tile translation;
- both distant public interfaces receive non-empty D3 relay reservations;
- final public marker coordinates are exact;
- component and anchored-interface validators pass;
- all original device entity ids survive serialization;
- assembler, requester-chest, and inserter control payloads survive serialization exactly.

The scenario is marked `acceptance` after this full pass so the heavyweight path remains opt-in. Routine CI continues to run the generic importer/geometry regressions and the final D4 head passed pytest, Ruff lint, Ruff format, and strict mypy.

### D4 in-game acceptance

The exact generated D4 probe was imported into Factorio and exercised manually. It behaved as designed:

- the complete assembler/device body remained rigid after translation;
- the distant green `recipe` interface changed the assembler recipe;
- the distant red `ingredients` interface reported the corresponding sanitized ingredient vector;
- the routed external seams were intact and usable in the real game.

The probe deliberately does not expose `enable` or `requester_demand`, so actual crafting is not part of D4 acceptance. The observed recipe/ingredient behavior is the intended end-to-end electrical smoke test.

### D acceptance

All D acceptance requirements are satisfied:

- a rigid multi-entity component participates in final placement/routing without losing internal geometry;
- hard component-owned, keepout, and adapter regions are enforced by the physical feasibility boundary;
- distant explicit public anchors receive validated relay workspace before routing;
- a real reusable device passes an end-to-end serialized integration test with exact payload preservation;
- the resulting serialized artifact has been imported and exercised successfully in Factorio.

## Milestone E — Oracle/device/layout unification

**Status: complete.**

**Goal:** let oracle providers and reusable external components participate in one physical composition story.

The oracle provider insertion point runs before signal allocation, wire-color assignment, placement, and routing. Providers can materialize ordinary free/anchored helper entities or validated reusable rigid components while using the same typed device and physical-layout contracts.

Standalone device generation remains useful for manual probes; compiler integration reuses the same device protocol and D1 physical component boundary rather than inventing a second device representation.

### E implementation sequence

1. **E1 — typed provider physical products [complete].** Provider materialization records ordinary helper entities as typed products and can carry a validated `ProviderRigidComponentProduct` containing an `ExternalDeviceBlueprint`, explicit prototype geometry, D1 regions/access points/legal origins, internal wire envelope, and device-port-to-abstract-net bindings. Binding helpers validate direction, Level modality, and scalar/vector shape. `lower_to_abstract_physical(...)` exposes the complete product set.
2. **E2 — unified pre-placement composition [complete].** Full `compile()` consumes rigid provider products before final routing. Component-local ids are rebased into compiler-global ids; typed device ports constrain their bound abstract nets to the required red/green wire color; scalar device signals precolor the ordinary DSATUR interference allocator; and exact opaque entity extents remain authoritative. Temporary connector proxies participate only in placement/electrical construction, then ordinary implementation is legalized away from component-owned geometry, routing is rebuilt with those regions excluded from relay workspace, and every proxy is replaced by the exact opaque device connector before D1/exact validation and opaque-aware serialization. Contradictory fixed-signal and wire-color constraints reject deterministically. Rigid providers currently remain at their declared geometry during this compiler path; D2 automatic origin search is not yet invoked.
3. **E3 — mixed integration benchmark [complete].** `examples/oracle_provider_mixed_probe.py` compiles one program containing ordinary scalar arithmetic, a freely placeable constant-oracle helper, a symbolically anchored constant-oracle endpoint at `(-12.5, -4.5)`, and the real 25-entity `AssemblerDevice` as a rigid provider component. A deterministic `recipe` vector is bound to the device's GREEN `recipe` port and the device's RED `ingredients` output realizes a vector oracle. The acceptance test checks all 25 opaque members, exact assembler/requester geometry, exact anchored-provider placement, ordinary arithmetic coexistence, required GREEN/RED net colors and connector ids, absence of E2 proxies, preserved assembler control payload, and one serialized Factorio blueprint.

E3 also exposed one feasibility gap: an explicit world anchor can lie outside the finite relay lattice used by the incremental joint annealer. Annealed vector synthesis now treats a retryable joint-bootstrap failure as an optimization failure and falls back to the ordinary constructive router on the same exact anchored placement seed. The constructive path searches world-space half-tile relay positions, preserves the anchor, and still validates the conservative external wire span. A small distant-anchor regression remains in routine CI; optimizing such off-lattice relay corridors inside the joint annealer remains optional future work.

### E automated evidence

The complete mixed E3 scenario passed once in the ordinary suite before being moved to the existing opt-in `acceptance` tier:

```text
581 passed, 33 skipped, 16 deselected in 124.92 s
Ruff lint: clean
Ruff format: 359 files already formatted
mypy: Success: no issues found in 139 source files
```

The durable E2/E3 construction and fallback contract is documented in `provider-composition.md`.

### E acceptance

All E acceptance requirements are satisfied:

- one compiler run jointly realizes ordinary logic, freely placeable provider helpers, an exact symbolic world anchor, and a real rigid reusable device component;
- typed device ports constrain the same abstract net-color/signal allocation used by ordinary compiler logic;
- final routing is performed before opaque serialization, with exact device endpoints restored and construction proxies removed;
- the serialized layout preserves component geometry, fixed anchors, conservative external wire reach, and opaque Factorio payloads;
- failure of an optional joint-annealing bootstrap no longer prevents a valid anchored constructive realization.

## Milestone F — Useful peripheral set

**Status: current.**

**Goal:** expand devices in an order that exercises new compiler capabilities.

Current main already contains the movement detector, packed-RGB lamp screen, and reusable assembler device. Suggested next additions are:

1. programmable speaker output;
2. roboport/logistic-stock vector reader;
3. belt/inserter pulse readers for Event integration;
4. richer machine/train interfaces after anchored macro placement is robust.

Prefer devices that double as integration benchmarks for the ABI, Event semantics, or anchored layout.

## Milestone G — Differential compiler fuzzing

**Goal:** compare reference semantics with compiled physical simulation automatically.

Generate random programs inside the currently supported semantic subset, including scalar/vector arithmetic, periodic and Event state, `sample_on`, `gate_clock`, `event_merge`, `sum_into`, `hold_into`, and output materialization policies. For a fixed input/oracle trace, compare semantic execution against physical simulation and shrink disagreements into minimal regression tests.

### G acceptance

- Seeded failures are reproducible.
- The shrinker can reduce common expression/state/clock mismatches.
- Unsupported language shapes are filtered or expected to reject explicitly.

## Milestone H — Further multilevel/global physical optimization

**Status: foundation landed during Milestone C; future optimization.**

**Goal:** improve quality/work beyond the accepted C baseline when larger applications justify it.

The core multilevel architecture is no longer speculative: relay-blind hypergraph coarsening, genuine coarse macro placement, macro annealing, hierarchical uncoarsening, and transactional fresh rerouting all landed as part of C. H is therefore the home for improvements that are useful but no longer block the physical ABI roadmap, for example:

- better coarse partitioning and multilevel objectives;
- directed boundary/inward compaction;
- routed cluster push-and-drag moves;
- stronger local legalization after singleton projection;
- multistart/seed selection with bounded cost;
- optional higher-density modes and the historical 80% Snake stretch target.

### H acceptance

- Large structural or application cases improve in quality or work compared with the accepted C baseline at equal validation guarantees.
- New density mechanisms remain circuit-generic and do not sacrifice fail-safe fallback behavior.

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

The immediate sequence is now:

```text
A. layout reliability corpus [complete]
    -> B. annealer observability [complete]
    -> C. annealing v2 / multilevel placement [complete]
    -> D. physical ABI placement integration [complete]
    -> E. oracle/device/layout unification [complete]
    -> F. useful peripheral set [current]
```

Milestone G should begin as soon as a small useful random-program generator exists and then grow continuously. H is optional optimization beyond the accepted C baseline and can proceed when a concrete application justifies it. I becomes especially valuable as D/E introduce richer rigid components and more serialized physical contracts.

## Current step

Proceed with **F1 — programmable speaker output**. Define a typed external-device protocol for alert/alarm playback that is small enough to compose through the completed E boundary, preserve exact speaker control payloads through opaque serialization, and use the implementation as the first Milestone F integration probe.
