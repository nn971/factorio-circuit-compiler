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

Before Milestone C is marked complete, provide an opt-in command:

```bash
uv run python -m benchmarks.milestone_c_acceptance
```

The command must print machine-readable-enough summaries and exit non-zero when a required
no-regression condition fails. It should compare the current implementation with the frozen pre-C
baseline using identical corpus cases, proposal budgets, and random seeds.

At minimum report, separately:

- relay count;
- occupied area;
- wire length;
- proposal attempts and rejection classes;
- routing-search work;
- runtime;
- better / equal / worse counts under the public lexicographic objective.

Do not collapse relay count, area, wire length, and work into one invented weighted score.

## Fixed structural corpus

The final acceptance bundle should include at least these representative families:

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

For a representative subset, additionally compare proposal budgets:

- 256;
- 1,024;
- 4,096;
- 16,384.

This distinguishes genuine search-efficiency improvement from a result that happens only at one
chosen budget. Report quality-versus-work rather than only final wall-clock time.

## Determinism

A fixed optimizer seed must produce the same optimized artifact across fresh Python processes and
multiple `PYTHONHASHSEED` values. The existing hash-determinism regression remains part of the gate.

The acceptance bundle should expose a direct command or pytest target for this check.

## Manual inspection bundle

Produce initial and optimized artifacts for a small set of informative examples, preferably:

- relay forest;
- clustered sparse cut;
- red/green mesh;
- narrow corridor;
- perimeter anchors;
- the 1k+ sparse case.

For each example include the public objective before/after. Where practical, export a blueprint or
other directly inspectable physical-layout representation so a player can visually check that metric
improvements correspond to sensible layouts.

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
