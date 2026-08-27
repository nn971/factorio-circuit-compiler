# Milestone C — acceptance results

This file records the durable acceptance evidence for **Milestone C — Annealing v2** against the frozen pre-C baseline.

## Frozen baseline

`a70df723768a6ba099ffd43017bdcb0291011c8f`

## Accepted behavior before final performance tuning

The retained Milestone C behavior is incremental exact mid-epoch best tracking, together with legacy-stable `RoutedWire` hashing that preserves the old CPython 3.12 / `PYTHONHASHSEED=0` annealing trajectory while remaining hash-seed deterministic.

The legacy-stable hash repair was validated first on the ordinary 8-case × 8-seed, 4,096-proposal gate:

- 2 better / 62 equal / 0 worse public lexicographic objectives;
- relay count unchanged in all 64 runs;
- occupied area unchanged in all 64 runs;
- two wire-length improvements;
- proposal, acceptance, rejection, routing-work, and topology-rebuild counters exactly unchanged from pre-C;
- median runtime ratio 1.049× pre-C.

## Full budget / scale gate

The documented command was then run from merged `main`:

```bash
uv run python -m benchmarks.milestone_c_acceptance \
  --seeds 8 \
  --proposals 4096 \
  --full \
  --json-report milestone-c-full-acceptance.json
```

The full matrix contains 101 baseline/current pairs:

- **6 better / 95 equal / 0 worse** public lexicographic objectives;
- relay count: 0 / 101 / 0 better/equal/worse;
- occupied area: 0 / 101 / 0;
- wire length: 6 / 95 / 0;
- proposal attempts: delta 0;
- accepted moves: delta 0;
- noop, geometry, wire-reach, and Metropolis rejections: all delta 0;
- routing priority-queue work: delta 0;
- topology rebuilds: delta 0;
- overall median runtime ratio: **1.031× pre-C**.

Improvements occur on the clustered sparse-cut family at every tested proposal scale:

| proposals | seed | pre-C wire length | current wire length |
| ---: | ---: | ---: | ---: |
| 256 | 1 | 59.571 | 51.830 |
| 1,024 | 2 | 55.142 | 40.487 |
| 4,096 | 3 | 34.372 | 34.136 |
| 4,096 | 5 | 61.635 | 53.268 |
| 16,384 | 1 | 44.899 | 42.264 |
| 16,384 | 2 | 47.741 | 37.672 |

The opt-in 1,200-object sparse fixture remained exactly equal in objective at 4,096 proposals and had a runtime ratio of approximately **0.998×**.

The acceptance-only GitHub Actions run was `33046453377`. Its retained artifact is `milestone-c-full-acceptance` (artifact id `9636057071`) with SHA-256 digest:

`e0ee4e50029406e6d83b8846a3fb0002cf2e73a44ab539919a5b62305d82926c`

The temporary acceptance PR was closed without merge; it changed no production behavior.

## Remaining performance observation

The full report also shows that exact accepted-move tracking still has avoidable overhead on some dense active cases even when the final objective is unchanged. In particular, the current hot loop computes `proposal_wire_length_delta()` before the Metropolis test, so feasible proposals that are later rejected still pay the exact-tracking wire-distance/sort cost.

The final Milestone C performance experiment is therefore deliberately trajectory-neutral: defer exact wire-length bookkeeping until after a proposal passes Metropolis but before state mutation. Acceptance requires identical optimized artifacts and work counters, together with a measurable runtime reduction against the immediate parent and a no-worse frozen pre-C gate.
