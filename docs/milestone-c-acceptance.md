# Milestone C acceptance

This document defines the user-verifiable exit gate for **Milestone C — Annealing v2**.
Milestone C is not complete merely because an individual heuristic merges: the final optimizer must be
compared reproducibly with the compiler state immediately before Milestone C.

## Frozen baseline

The pre-C baseline is commit:

`a70df723768a6ba099ffd43017bdcb0291011c8f`

This is the main-branch A+B merge before the Milestone C annealing experiments began. Do not silently
move this baseline forward when later C improvements merge.

## One-command acceptance

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

The final acceptance bundle includes these representative families:

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

Heavy multi-seed and 1k+ acceptance runs remain opt-in rather than making routine CI a benchmark
suite.

## Experiment history

Record accepted and rejected Milestone C experiments with their paired-run evidence. A rejected
experiment should not remain as dormant production knobs unless it has an independent reason to
exist.

The final Milestone C report should make it possible to answer both:

1. what did the final optimizer improve over the frozen pre-C baseline? and
2. how much extra work or runtime did those improvements cost?
