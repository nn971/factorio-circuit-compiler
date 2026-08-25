# Annealed joint-layout handoff

## 2026-08-26 generic routed-layout continuation

The optimizer now has a public generic input boundary in
`src/factorio_circuit/synthesis/layout_optimizer.py`. It accepts a complete valid `Layout` plus its
legal lattice, reserved areas, fixed object coordinates, and conservative reach limit. Budget zero
returns the exact input after authoritative validation. Positive optimization seeds the existing
incremental topology directly from input wires, retains the input as best-known state, and uses a
transactional generic coarse compaction/reroute before local annealing. Coarse placement traverses
the concrete physical-net hypergraph and places each entity near already-placed electrical peers;
input row order and safe-folded construction order are not placement policies. Fixed public markers
define a front-panel line and the implementation is placed on its circuit-facing side.

The full safe-folded Snake (13,862 relays, area 135,124.0) reaches 245–249 relays and area
5,828–6,014 across fixed seeds 0–2 with 512 proposals. In every serialized result the eleven public
markers are the only entities on the `y=0` perimeter; reset, movement, and framebuffer remain at
`(0,0)`, `(3,0)`, and `(6,0)`. See `docs/annealed-layout-experiments.md` for exact measurements. The
benchmark command is:

```bash
uv run python -m benchmarks.snake.generate \
  --anneal-safe-folded-seed \
  --annealing-iterations 512 \
  --layout-seed 0 \
  --layout-retries 1 \
  --census \
  --output /tmp/snake-generic-front-panel-512-seed0.txt
```

The generated artifacts pass the new authoritative physical validator. They have not yet been
tested in Factorio; do not treat structural generation alone as target-level acceptance.

An unrelated opt-in corpus is available through
`uv run python -m benchmarks.layout_optimizer_corpus --proposals 256 --seed 0`. Its largest case
contains 200 implementation entities plus 1,900 hand-authored relays and completes in about 0.8s;
see `docs/annealed-layout-experiments.md` for the three-seed results.

This file is the continuation note for PR #34 (`agent/annealed-joint-layout`). It records the current validated state, the architecture that should be preserved, and the next optimization targets. Read `AGENTS.md`, `docs/data-contract.md`, and `docs/compiler-pipeline.md` first; this file is branch-scoped and intentionally more operational.

## Status

PR: #34, `Improve annealed physical layout and shared relay placement`

Branch: `agent/annealed-joint-layout`

The current annealed path has been validated in game on the full 16x16 random-food Snake after the relay-position and I/O-anchor fixes. The previously broken 1000-proposal build now behaves normally.

Last known successful heavyweight command:

```bash
git switch agent/annealed-joint-layout
git pull --ff-only

uv run python -m benchmarks.snake.generate \
  --annealing-layout \
  --annealing-iterations 1000 \
  --corridor-width 4 \
  --layout-retries 1 \
  --census \
  --output snake-annealed-fixed.txt
```

Observed successful run:

```text
implementation combinators: 602
layout relays:             1157
state period:              65
physical groups routed:    501
candidate area:            9945.0
candidate wire length:     9898.3
reset marker:              (-2.0, 56.0)
movement marker:           (-2.0, 57.0)
framebuffer marker:        (114.0, 52.0)
```

The generated blueprint was tested in Factorio and Snake movement/display behavior was normal. The framebuffer output is now on the actual right perimeter rather than buried at the pre-expansion boundary.

The routine CI immediately before cleanup was fully green:

```text
445 passed, 33 skipped, 14 deselected
Ruff lint: pass
Ruff format: pass
mypy src: pass (119 source files)
```

Re-check CI after any continuation commit; do not treat the numbers above as a permanent baseline.

## Production path

The production vector synthesis path is:

```text
semantic/lowering
    ↓
AbstractPhysicalCircuit
    ↓
red/green net coloring + concrete signal allocation
    ↓
physical combinator materialization
    ↓
deterministic implementation-only placement seed
    ↓
relay-capacity prepass / common candidate envelope
    ↓
refine_incremental_joint_layout(...)
    ↓
explicit reach-safe bootstrap
    ↓
reach-preserving joint annealing
    ↓
epoch-local relay pruning/bypass
    ↓
final artifact validation
    ↓
Layout / blueprint serialization
```

The safe-folded crossbar remains the correctness/reference backend and is useful for A/B diagnosis. The current random-food safe-folded Snake behaves normally.

## Current feasible-first architecture

The joint optimizer deliberately does **not** anneal an infeasible Euclidean surrogate and hope to repair it afterward.

The intended flow is:

```text
implementation seed
      │
      ├── dense seed already routable → use it
      │
      └── dense seed cannot bootstrap
              ↓
       porous implementation seed
       on the same corridor-aware grid
              ↓
       explicit reach-safe shared-net topology
              ↓
       implementation + relays
              ↓
       local reach-preserving annealing
              ↓
       epoch-local relay simplification
              ↓
       final validated routing snapshot
```

### Bootstrap

`src/factorio_circuit/synthesis/incremental_joint_layout.py` builds one explicit reach-safe topology before annealing starts.

Important properties:

- Relay sites are legal 1x1 `grid.unit_slots`.
- Implementation entities and relays share the same reserved-corridor policy.
- If the dense implementation seed locally traps terminals, the porous bootstrap places implementation entities on alternating rows/columns, leaving connected relay lanes.
- If the candidate grid must grow, automatic I/O anchors are recomputed from the new grid bounds before routing. Explicit anchors take precedence.
- Relay path search uses a cached `_RelayWorkspace` bucket index; ordinary neighbor lookup is local rather than scanning every vacant site.
- Physical net groups are routed sequentially. On failure, the current implementation performs a small number of deterministic global rip-up/reorder attempts before giving up.

### Annealing hot loop

The expensive exact physical-net graph must not be rebuilt for every proposal.

`_FeasibleTopology` caches:

- the current explicit routing snapshot,
- incident wires by object,
- topology neighbors,
- current wire energy.

A proposal for one object (or a same-footprint swap) checks only the affected incident wires. If any proposed wire exceeds `safe_span`, reject immediately. This keeps ordinary proposal cost proportional to local topology degree rather than physical-net size.

The soft wire objective is currently:

```python
normalized = distance / safe_span
slack_pressure = max(0.0, normalized - 0.85)
wire_energy = 0.12 * normalized + 4.0 * slack_pressure**2
```

The hard constraint remains `distance <= safe_span`.

Every `_EPOCH_PROPOSALS = 256` proposals, the local topology simplifier removes degree-0/degree-1 relay garbage and bypasses a degree-2 relay when its neighbors can connect directly within the safe span. It does not perform the former all-pairs exact topology refresh.

## Critical correctness invariants

These are not optional implementation details.

### 1. Relay geometry has one source of truth while optimizing

`_JointState.relay_positions` is authoritative during joint optimization.

A previous regression kept mutable relay positions in `_JointState` but stale `BlueprintRelay.position` values inside cached `RoutingPlan` objects. Annealing validated the moved geometry, then serialization emitted the old geometry. This caused the 1000-proposal Snake to misbehave while the zero-iteration build worked.

Current code synchronizes/materializes relay coordinates from `_JointState` whenever a topology snapshot is built and again at final return. Final validation checks the exact coordinates that will be serialized.

Do not reintroduce an independent mutable relay-coordinate copy in cached topology state.

### 2. Automatic I/O anchors follow workspace expansion

The initial placer anchors public inputs on the left perimeter and outputs on the right perimeter. The porous bootstrap may enlarge the candidate grid. Automatic I/O positions therefore have to be recomputed from the expanded bounds before routing starts.

The bug symptom was:

```text
inputs still at x=-2
outputs stuck around old x=54
actual expanded circuit extended to about x=92
```

The fixed full Snake instead reported the framebuffer at x=114 on the real right edge.

Explicit user anchors must remain fixed and override automatic anchors.

### 3. Final validation must validate the serialized artifact

Do not validate one position dictionary and serialize another. Build the final position map with `routed_positions(...)`, then validate wire spans and relay/entity clearance against that artifact.

### 4. Green CI is not sufficient for heavyweight layout correctness

The stale-relay bug passed ordinary tests and structural validation. The decisive acceptance test was a real Factorio Snake A/B.

For changes to joint movement, topology snapshots, relay simplification, bootstrap placement, or routing, run the heavyweight Snake command when practical and test it in game.

## Diagnostic history worth retaining

Several hypotheses were ruled out by controlled A/B tests:

- **Temporal/lowering regression:** ruled out because the current safe-folded Snake behaves normally.
- **Random Input selector/oracle:** ruled out by replacing only the random selector operation with deterministic Select Input; the broken annealed layout still had the same funny movement.
- **The 23 same-entity input→output wires:** ruled out because the working zero-iteration annealed blueprint has exactly the same 23 feedback wires.
- **Initial joint bootstrap:** zero-iteration annealed Snake behaved normally. The gameplay bug was introduced only when the joint refinement actually moved objects.
- **`safe_span=7` as the primary Snake bug:** no longer favored. Vanilla arithmetic/decider/constant combinators have enough circuit reach margin that the stale serialized relay geometry was a much stronger and directly demonstrated cause.

The zero-vs-1000 experiment was particularly useful:

```text
annealed iterations = 0       → works in Factorio
annealed iterations = 1000    → previously broken
```

The production implementation seed itself is generated with `iterations=0`; the requested iteration count is passed to `refine_incremental_joint_layout`, so that A/B really isolated joint refinement.

## Known routing/bootstrap debt

### Cross-net relay-site contention

The porous bootstrap fixed local terminal burial, but sequential routing can still fail because earlier nets reserve relay sites needed by later nets.

Observed failures had many globally free sites and nonzero free neighbors around both endpoints, for example:

```text
free_sites > 29k
source_free_neighbors > 0
target_free_neighbors > 0
claimed_relays ≈ 1000–1500
```

That means the failure is not “no workspace” and not “terminal cannot escape”; it is cross-net congestion.

Current mitigation: bounded deterministic global rip-up/reorder attempts.

Recommended future direction if this remains a real bottleneck: negotiated-congestion routing (FPGA-style) or bounded targeted rip-up of conflicting nets. Do not keep adding arbitrary net-order heuristics indefinitely.

### Relay-capacity prepass

`open_vector._joint_capacity_fill()` still estimates relay demand and enlarges the initial common envelope before the feasible bootstrap. The porous bootstrap can also expand its workspace independently, so this prepass may be partly redundant.

Do **not** remove it as cleanup without benchmark evidence: the in-game-validated Snake above used the current prepass. Compare generation success, relay count, area, wire length, and runtime with/without the prepass first.

### Exact connector-aware reach validation

The compiler currently uses a conservative center-to-center `safe_span` model. It is intentionally below vanilla combinator reach and was not the primary cause of the recent Snake bug.

Long-term, a Factorio-side acceptance probe using the actual connector API (`can_wire_reach`) would still be valuable as an independent target-level validator.

## Optimization targets for the next context

Preserve correctness first; then measure. Good next targets are:

1. **Objective quality.** The validated fixed Snake used 1157 relays and area 9945.0, worse than the pre-fix stale-artifact report (which was not a trustworthy physical score). Treat 1157 / 9945.0 / 9898.3 as the first trustworthy in-game-valid baseline for this exact command.
2. **Proposal quality / acceptance.** Instrument accepted/rejected proposals by reason: wire reach, occupancy, illegal site, Metropolis rejection, no-op. This will show whether runtime is spent proposing impossible moves.
3. **Relay movement neighborhood.** Current relay proposals snap to legal unit sites but include random global jumps. Compare local/centroid-biased neighborhoods against acceptance rate and final relay count.
4. **Epoch policy.** `_EPOCH_PROPOSALS=256` is a heuristic. Measure simplifier cost and benefit; exact work belongs at coarse boundaries, not in the hot loop.
5. **Topology simplification.** The local degree-2 bypass is cheap but conservative. Consider richer local rewrites only if they can be bounded and feasibility-preserving.
6. **Bootstrap routing contention.** If generation failures remain common under harder circuits or tighter safe spans, replace reorder fallback with negotiated congestion.
7. **Capacity prepass.** Benchmark whether `_joint_capacity_fill()` still earns its complexity.
8. **Candidate scoring.** Current retry selection is lexicographic `(relay_count, area, wire_length, restart)`. Keep relay count dominant unless a different explicit physical objective is chosen.

Avoid optimizing based on the old broken run's `relays=1128; area=8117.5; wire=9661.7`: those metrics were computed from stale serialized relay positions and therefore were not a valid candidate-quality reference.

## Useful commands

Routine validation:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

Focused layout regressions:

```bash
uv run pytest tests/synthesis/test_incremental_joint_regressions.py
uv run pytest tests/integration/test_layout_benchmark_examples.py
```

Heavyweight annealed Snake:

```bash
uv run python -m benchmarks.snake.generate \
  --annealing-layout \
  --annealing-iterations 1000 \
  --layout-retries 1 \
  --census \
  --output snake-annealed-blueprint.txt
```

Zero-proposal bootstrap control:

```bash
uv run python -m benchmarks.snake.generate \
  --annealing-layout \
  --annealing-iterations 0 \
  --layout-retries 1 \
  --census \
  --output snake-annealed-zero-iterations.txt
```

Safe-folded correctness reference:

```bash
uv run python -m benchmarks.snake.generate \
  --layout-retries 1 \
  --census \
  --output snake-safe-folded-current.txt
```

## Files to read before modifying the optimizer

- `src/factorio_circuit/synthesis/incremental_joint_layout.py` — feasible bootstrap, cached topology, local annealing, simplifier.
- `src/factorio_circuit/synthesis/open_vector.py` — production synthesis orchestration, capacity prepass, retries, candidate selection.
- `src/factorio_circuit/synthesis/placement.py` — corridor-aware legal grid, 2x1 vs 1x1 occupancy, automatic I/O anchors.
- `src/factorio_circuit/synthesis/joint_layout.py` — exact/shared helper primitives used by incremental layout; older joint implementation also remains here.
- `src/factorio_circuit/blueprint/routing.py` — relay/wire artifact types and final span/clearance helpers.
- `tests/synthesis/test_incremental_joint_regressions.py` — regressions for stale relay snapshots, expanded I/O anchors, and global bootstrap reorder.
- `benchmarks/snake/generate.py` — heavyweight physical acceptance generator.

## Cleanup boundary

The recent cleanup intentionally does not change the validated layout algorithm. It updates stale diagnostics/documentation and records the benchmark-sensitive debts instead of deleting them blindly.

Before large optimization work, prefer creating a fresh branch/context from the current PR head after CI is green. Keep PR #34 as the correctness milestone unless there is a reason to continue piling optimization experiments into the same review.
