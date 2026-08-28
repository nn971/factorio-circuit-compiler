# Milestone C acceptance

**Status: accepted / complete.**

This document records the user-verifiable exit criteria and final acceptance evidence for **Milestone C — Annealing v2 / multilevel physical placement**.

Milestone C was accepted on 2026-08-28 after the current multilevel Snake artifact was imported into Factorio, exercised successfully, and judged physically satisfactory in game. The accepted artifact is the seed-0 C3 -> C4 -> C6 -> fresh C5 result described below.

## Acceptance philosophy

Milestone C is not accepted merely because an individual heuristic merges, nor merely because the optimizer is no worse than a frozen structural baseline. Acceptance combines:

1. structural regression protection;
2. a fail-safe and exact-valid physical optimization contract;
3. a large application result showing that the optimizer is practically useful;
4. in-game validation of the serialized artifact.

Physical occupancy remains a primary quality metric, but there is **no universal hard occupancy percentage required for correctness or roadmap completion**. The earlier strictly-greater-than-80% target was a provisional stretch target introduced after the first Snake application result remained near 4% occupancy. It successfully forced the project toward a true multilevel architecture, but it proved unnecessarily strict as a milestone boundary once the 65.12% artifact was compact, reliable, and satisfactory in actual play.

The historical 80% target remains useful as an optional future optimization benchmark.

## Application-level acceptance contract

### Failproof starting point and fallback

Physical optimization must have a validated complete layout available as a fail-safe starting point or fallback. Recoverable optimization, legalization, or rerouting failures must not turn a valid compiler result into an invalid one.

The retained optimizer boundary therefore exact-validates candidates and preserves the validated input/best-known result transactionally.

### Exact serialized validity

The accepted final artifact must satisfy the compiler's authoritative physical checks, including:

- entity footprint legality and non-overlap;
- fixed positions and legal placement lattice;
- connector identity;
- red/green electrical topology;
- wire reach;
- serialized coordinates and wires matching the validated `Layout`.

Application acceptance is performed on the actual serialized blueprint, not only on an abstract placement score.

### Generality

The optimization must remain a general circuit-layout algorithm:

- no assumption about Snake's logical structure, framebuffer organization, row/column shape, or other application-specific facts;
- no Snake-specific scoring term, proposal, cluster, or hand-authored placement hint;
- placement mechanisms are derived from general physical/netlist information;
- fixed anchors, mixed footprints, logical nets, routing, and legalization remain authoritative compiler concepts.

Relay-blind hypergraph coarsening, macro placement/annealing, hierarchical uncoarsening, transactional rerouting, and general local refinement satisfy this requirement when implemented independently of the benchmark circuit.

### Relay efficiency and simplification

The final routed artifact must be simplified under the compiler's general topology-preserving relay eliminations rather than retaining a known-unnecessary failproof scaffold.

This does not require a globally minimum Steiner network. It requires the final pipeline to remove relay structure that its general simplifier knows how to eliminate and to report routing overhead explicitly.

For application reports, retain:

```text
implementation combinators / relay combinators
```

as an easy-to-read routing-efficiency metric alongside relay count itself.

### Bounded practical convergence

Application reports should publish at least:

- implementation and public-marker counts;
- relay count;
- physical footprint numerator;
- exact occupied bounding-box area;
- physical occupancy ratio;
- routed wire length;
- relevant proposal/routing work;
- wall-clock runtime and execution environment when available.

No fixed wall-clock SLA is imposed because hardware differs, but effectively unbounded search is not an acceptable application result.

### In-game acceptance

At least one Snake-scale application artifact must be imported and exercised in the real game. Exact compiler validation is necessary, but the milestone also requires evidence that the serialized result behaves correctly and is practically usable as a physical blueprint.

## Accepted Snake reference result

The accepted seed-0 C3 -> C4 -> C6 -> fresh C5 pipeline produced:

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

The final blueprint was imported into Factorio and behaved correctly. Its physical layout was also judged satisfactory in game. This is the accepted Milestone C application reference.

## Why the old 80% threshold was retired

The first flat-annealer Snake check was correct but extremely sparse: about 4.0% physical occupancy with 2,482 relays in a 91,805-tile bounding box after 4,096 proposals. Ordinary proposal-count increases showed strong diminishing returns.

The old >80% target was useful because it made that result impossible to rationalize as "good enough" and motivated the multilevel redesign. The redesign then changed the problem qualitatively:

- logical-net coarsening reduced hundreds of implementation objects to a small macro problem;
- coarse contraction established the correct global scale;
- macro annealing improved area, logical-net HPWL, and congestion before relay routing;
- nested hierarchical uncoarsening preserved the compact envelope instead of scattering singleton entities;
- fresh transactional rerouting rebuilt the final connectivity from zero relays and exact-validated it.

The resulting 65.12% artifact is already compact and useful in game. Requiring an additional arbitrary 15 percentage points would keep Milestone C open for fine density work without demonstrating a missing compiler capability. That work is therefore optional future optimization rather than an exit dependency.

## Frozen structural baseline

The pre-C baseline remains:

`a70df723768a6ba099ffd43017bdcb0291011c8f`

This is the A+B main-branch state before the Milestone C experiments began. Do not silently move this baseline forward when later optimization work lands.

## Structural regression acceptance

Run the standard multi-seed comparison with:

```bash
uv run python -m benchmarks.milestone_c_acceptance
```

If the frozen commit is not present in a shallow clone:

```bash
git fetch origin a70df723768a6ba099ffd43017bdcb0291011c8f
```

Run the heavier budget-curve and scale comparison with:

```bash
uv run python -m benchmarks.milestone_c_acceptance --full
```

To retain machine-readable results:

```bash
uv run python -m benchmarks.milestone_c_acceptance \
  --json-report /tmp/milestone-c-acceptance.json
```

These reports keep relay count, occupied area, wire length, proposal/rejection work, routing-search work, runtime, and better/equal/worse outcomes separate rather than inventing one weighted score. The public comparison remains the existing lexicographic physical objective.

The completed full structural gate contained 101 baseline/current pairs and produced **6 better / 95 equal / 0 worse** public lexicographic outcomes, with no relay-count or area regressions and six wire-length improvements. The overall median runtime ratio was **1.031x** the frozen baseline.

## Determinism

A fixed optimizer seed must produce the same optimized artifact across fresh Python processes and multiple `PYTHONHASHSEED` values. Run the dedicated regression with:

```bash
uv run pytest tests/synthesis/test_layout_hash_determinism.py
```

## Manual structural inspection

Generate three-way structural layouts with:

```bash
uv run python -m benchmarks.milestone_c_examples /tmp/milestone-c-layouts
```

The generated initial / frozen pre-C / current SVGs are visual regression aids. They complement, rather than replace, exact validation and the real Factorio application check.

## Repository gate

Retained Milestone C code must continue to pass ordinary repository checks:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

Heavy multi-seed, scale, and Snake application runs remain opt-in rather than burdening routine CI.

## Future density work

Further density improvements are welcome but are not Milestone C blockers. In particular, the historical 80% Snake target, fine routed compaction, directed inward moves, push-and-drag cluster motion, stronger singleton legalization, and multistart strategies belong to later multilevel/global optimization work unless a future application exposes a concrete need for them.
