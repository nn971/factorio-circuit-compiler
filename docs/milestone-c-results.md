# Milestone C — acceptance results

This file records the durable acceptance evidence for **Milestone C — Annealing v2** against the frozen pre-C baseline.

## Frozen baseline

`a70df723768a6ba099ffd43017bdcb0291011c8f`

## Accepted behavior

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

## Final performance experiment

The full report showed noticeable exact-tracker overhead on some dense active cases, so one final trajectory-neutral optimization was tested: defer `proposal_wire_length_delta()` until after a proposal passes the Metropolis test.

The candidate passed the full test suite and an immediate-parent paired benchmark confirmed **32 / 32 identical complete layout fingerprints**. It therefore preserved the search trajectory exactly. It did not, however, provide a measurable speedup:

- overall median runtime ratio: **1.003×** the immediate parent;
- relay forest: 0.997×;
- shared bus: 1.005×;
- clustered sparse cut: 0.995×;
- red/green mesh: 1.004×;
- near-optimal packed: 1.024×;
- narrow corridor: 1.000×;
- perimeter anchors: 1.007×;
- fixed endpoint span: 1.001×.

The experiment was therefore **rejected**. The production hot loop remains unchanged: the tiny theoretical saving was below benchmark noise and did not justify another retained code path.

## Exit conclusion

Milestone C exits with a deterministic, feasibility-preserving annealer that retains transient exact-objective improvements without changing the intended proposal trajectory. Against the frozen pre-C baseline it is no worse across every tested structural case, seed, budget, and the 1,200-object scale fixture, while producing six measured wire-length improvements at unchanged relay count and occupied area in the full acceptance matrix.

The reproducible acceptance commands and manual SVG comparison workflow remain documented in `milestone-c-acceptance.md`.
