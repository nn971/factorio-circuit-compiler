# Milestone C acceptance

This document defines the user-verifiable exit gate for **Milestone C — Annealing v2**.
Milestone C is not complete merely because an individual heuristic merges, nor merely because the
optimizer is no worse than a frozen structural baseline. Final acceptance requires both the structural
regression gates below and the application-level density/convergence contract in this document.

## Hard application-level acceptance contract

Milestone C is accepted only when the general-purpose physical annealer satisfies all of the following.

### Failproof starting layout

The annealer must start from a validated failproof physical layout policy, such as
`safe-folded-crossbar` or `safe-crossbar`, and retain the validated input/best-known layout as its
fallback. The acceptance result must therefore demonstrate optimization of an already complete,
reach-safe routed circuit rather than relying on a fragile constructive seed that happens to work for
the benchmark.

### Greater than 80% physical occupancy

When applied to Snake or another comparably large application circuit, the final blueprint must have
**strictly greater than 80% physical occupancy**.

For acceptance reporting, physical occupancy is measured from the exact serialized/validated layout as:

```text
sum of physical entity footprint areas
---------------------------------------
occupied axis-aligned bounding-box area
```

The numerator includes implementation combinators, relay combinators, and other placed physical
entities that consume placement footprint. The denominator uses the exact outer footprint bounds of
the same final artifact. Wire graphics do not count as occupied tile area.

The benchmark report must publish the numerator, denominator, and ratio explicitly rather than infer
density from a screenshot.

### Generality

The optimization that achieves the density target must remain a general circuit-layout algorithm:

- no assumption about Snake's logical structure, geometry, row/column shape, framebuffer organization,
  or other application-specific facts;
- no assumption that the input circuit has a particular global shape;
- no Snake-specific scoring term, proposal, clustering rule, seed, or hand-authored placement hint;
- no non-general compaction move whose applicability depends on a special geometric coincidence, such
  as treating empty-strip squeezing as a primary optimization operation;
- global zooming, multilevel/coarse clustering, macro annealing, transactional moves, legalization, and
  similar techniques are acceptable only when defined from general physical/netlist information and
  applicable to arbitrary circuits satisfying the compiler's physical contracts.

A benchmark-specific harness may select the input circuit and record measurements, but it may not
change optimizer behavior specifically for that benchmark.

### No redundant relay combinators

The final routed artifact must contain no relay combinator that is known to be removable while
preserving required electrical connectivity, connector identity, footprint legality, and wire reach.
At minimum, the final topology must be simplified to a fixed point under all general relay-redundancy
eliminations implemented by the optimizer, including isolated-relay deletion, useless leaf deletion,
and legal degree-two bypass. No deliberately redundant relay scaffold from the failproof seed may be
left merely because the optimizer did not revisit it.

This requirement does not claim that the compiler must solve a globally minimum Steiner-routing
problem. It does require that every relay left by the accepted pipeline survives the compiler's
general redundancy checks and that no known removable relay is retained.

### Practical convergence at Snake scale

The accepted algorithm must converge to the >80% occupancy result in a **reasonable bounded time** on
circuits of Snake scale. Acceptance reports must publish at least:

- implementation combinator count;
- relay combinator count;
- physical occupancy numerator/denominator/ratio;
- occupied bounding-box area;
- routed wire length;
- proposal/work budget and relevant routing-work counters;
- wall-clock runtime and execution environment.

No fixed wall-clock SLA is imposed here yet because hardware differs, but an acceptance run that
requires impractical or effectively unbounded search does not pass. The runtime is part of the user
acceptance decision rather than an informational-only number.

### Relay efficiency as an optimization quality metric

For otherwise comparable valid layouts, a larger ratio

```text
implementation combinators / relay combinators
```

is considered better. Relay count remains the first component of the existing public lexicographic
objective, while this ratio is reported explicitly on application benchmarks because it makes routing
overhead easy to interpret at circuit scale. A relay-free result may be reported as an infinite ratio.

### Application acceptance is mandatory

Structural corpus success is necessary but not sufficient. At least one Snake-scale application must
pass the complete contract above before Milestone C may be marked complete. Snake is the canonical
application benchmark while it remains representative of the compiler's largest practical workload;
additional applications may be added to prevent overfitting.

## Frozen baseline

The pre-C baseline is commit:

`a70df723768a6ba099ffd43017bdcb0291011c8f`

This is the main-branch A+B merge before the Milestone C annealing experiments began. Do not silently
move this baseline forward when later C improvements merge.

The frozen structural comparison is a regression guard. Passing it does **not** waive the hard
application-level acceptance contract above.

## One-command structural acceptance

Run the standard multi-seed comparison with:

```bash
uv run python -m benchmarks.milestone_c_acceptance
```

The command creates a detached git worktree at the frozen baseline and runs baseline/current
optimizers in separate Python processes so modules from the two revisions cannot contaminate one
another. Both subprocesses use a fixed `PYTHONHASHSEED`, every produced layout is validated, and the
command exits non-zero if the current optimizer loses any required public lexicographic objective.

If the frozen commit is not present in a shallow clone, fetch it explicitly before running the gate:

```bash
git fetch origin a70df723768a6ba099ffd43017bdcb0291011c8f
```

Run the heavier budget-curve and scale gate with:

```bash
uv run python -m benchmarks.milestone_c_acceptance --full
```

To retain the complete rows and summary for later inspection, add for example:

```bash
uv run python -m benchmarks.milestone_c_acceptance \
  --json-report /tmp/milestone-c-acceptance.json
```

The report keeps these measurements separate rather than inventing one weighted score:

- relay count;
- occupied area;
- wire length;
- proposal attempts and rejection classes;
- routing-search work;
- runtime;
- better / equal / worse counts under the public lexicographic objective.

It also reports relay-count, occupied-area, and wire-length outcomes independently. The public pass/fail
criterion remains the existing lexicographic objective rather than those independent component counts.

## Fixed structural corpus

The structural acceptance bundle includes these representative families:

- relay forest;
- shared bus;
- clustered sparse cut;
- red/green mesh;
- near-optimal packed seed;
- narrow constrained corridor;
- perimeter anchors;
- fixed endpoint span;
- a 1k+ sparse scale case for opt-in scale validation.

All produced layouts must pass `validate_physical_layout`.

## Multi-seed and budget curves

Use a fixed documented seed set. Eight seeds is the default minimum for ordinary structural cases.

The `--full` gate additionally compares representative cases at proposal budgets:

- 256;
- 1,024;
- 4,096;
- 16,384.

This distinguishes genuine search-efficiency improvement from a result that happens only at one
chosen budget. Report quality-versus-work rather than only final wall-clock time. Runtime ratios use
the statistical median; routing/proposal work counters are the more reproducible performance signal.

## Determinism

A fixed optimizer seed must produce the same optimized artifact across fresh Python processes and
multiple `PYTHONHASHSEED` values. The existing hash-determinism regression remains part of the gate.

Run it directly with:

```bash
uv run pytest tests/synthesis/test_layout_hash_determinism.py
```

## Manual inspection bundle

Generate directly inspectable three-way layouts with:

```bash
uv run python -m benchmarks.milestone_c_examples /tmp/milestone-c-layouts
```

For each case this writes **initial**, **frozen pre-C optimized**, and **current optimized** SVGs,
plus `manifest.json` with exact objectives and an `index.html` showing the three artifacts side by
side. The default set contains:

- relay forest;
- clustered sparse cut;
- red/green mesh;
- narrow corridor;
- perimeter anchors.

Add the 1,200-object sparse case with:

```bash
uv run python -m benchmarks.milestone_c_examples /tmp/milestone-c-layouts --include-scale
```

The SVGs preserve actual entity/relay coordinates and physical red/green wires. They are intended as
a visual sanity check that a current-vs-pre-C objective improvement corresponds to a sensible physical
layout rather than a metric artifact.

## Verifier regression tests

The lightweight acceptance-tool regressions are ordinary pytest tests and do not run the multi-seed
benchmark itself:

```bash
uv run pytest tests/synthesis/test_milestone_c_acceptance_tools.py
```

They cover request uniqueness/budget expansion, lexicographic and per-component reporting, and basic
SVG rendering.

## Repository gate

The final retained C implementation must also pass the ordinary repository checks:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

Heavy multi-seed, application, and 1k+ acceptance runs remain opt-in rather than making routine CI a
benchmark suite.

## Experiment history

Record accepted and rejected Milestone C experiments with their paired-run evidence. A rejected
experiment should not remain as dormant production knobs unless it has an independent reason to
exist.

The final Milestone C report should make it possible to answer all of:

1. what did the final optimizer improve over the frozen pre-C baseline?
2. how much extra work or runtime did those improvements cost?
3. does the general optimizer exceed 80% physical occupancy on a Snake-scale application from a
   failproof seed?
4. how much relay overhead remains relative to implementation combinators?
