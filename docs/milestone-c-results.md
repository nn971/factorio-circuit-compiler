# Milestone C — structural stabilization results

This file records durable **structural stabilization evidence** for **Milestone C — Annealing v2** against the frozen pre-C baseline. These results are necessary regression evidence, but they are no longer sufficient for final Milestone C acceptance. The hard application-level exit contract is defined in `milestone-c-acceptance.md`.

## Frozen baseline

`a70df723768a6ba099ffd43017bdcb0291011c8f`

## Retained behavior

The retained Milestone C behavior is incremental exact mid-epoch best tracking, together with legacy-stable `RoutedWire` hashing that preserves the old CPython 3.12 / `PYTHONHASHSEED=0` annealing trajectory while remaining hash-seed deterministic.

The legacy-stable hash repair was validated first on the ordinary 8-case × 8-seed, 4,096-proposal gate:

- 2 better / 62 equal / 0 worse public lexicographic objectives;
- relay count unchanged in all 64 runs;
- occupied area unchanged in all 64 runs;
- two wire-length improvements;
- proposal, acceptance, rejection, routing-work, and topology-rebuild counters exactly unchanged from pre-C;
- median runtime ratio 1.049× pre-C.

## Full budget / scale structural gate

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

## Snake application check: current gap

The current generic annealer was also applied to the production Snake circuit from a complete `safe-folded-crossbar` routed seed. The heavyweight Snake semantic acceptance passed, and the emitted annealed blueprints were manually tested in Factorio and behaved correctly.

The 4,096-proposal seed-0 result was:

```text
implementation combinators   602
relay combinators            2,482
occupied bounding-box area  91,805 tiles²
routed wire length          19,405.0
annealer runtime               480.0 s
```

Using the final blueprint's entity footprints, the approximate occupied footprint is:

```text
591 wide combinators × 2 tiles   = 1,182
22 one-tile constants            =    22
2,482 one-tile relays            = 2,482
                                      -----
occupied footprint               = 3,686 tiles²
```

so physical occupancy is approximately:

```text
3,686 / 91,805 ≈ 4.0%
```

This is far below the **strictly greater than 80%** application-level requirement now defined in `milestone-c-acceptance.md`. The result demonstrates that the current annealer is on a useful optimization path—it removes most of the failproof seed's relay scaffold and greatly reduces wire length—but it does **not** satisfy the final density/convergence contract.

The application check also showed diminishing returns from ordinary flat proposals: increasing the budget from 512 to 4,096 improved relay count from 2,586 to 2,482 and occupied area from 93,632 to 91,805, but the layout remained extremely sparse. This is evidence that the remaining problem is convergence/move scale rather than simply insufficient proposal count.

## Current conclusion

Milestone C is **not accepted yet**.

The structural work completed so far establishes a deterministic, feasibility-preserving, well-instrumented local annealer with strong regression protection. Final acceptance additionally requires a general-purpose optimizer that starts from a failproof layout and converges on a Snake-scale application to >80% physical occupancy, without circuit-shape assumptions, Snake-specific behavior, non-general compaction tricks, or known-redundant relays, in a practical runtime.

The reproducible structural commands, the hard application-level contract, and manual SVG comparison workflow are documented in `milestone-c-acceptance.md`.
