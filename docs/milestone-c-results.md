# Milestone C — final results

This file records the durable evidence for **Milestone C — Annealing v2 / multilevel physical placement** against the frozen pre-C baseline and the final accepted Snake application artifact.

**Milestone C is complete.**

## Frozen baseline

`a70df723768a6ba099ffd43017bdcb0291011c8f`

This is the A+B main-branch state before the Milestone C optimizer work began.

## Structural stabilization

The early C work first made the existing feasible joint annealer safer to change and easier to evaluate.

Retained pieces include incremental exact mid-epoch best tracking and legacy-stable `RoutedWire` hashing. The exact tracker retains transient improvements under the public lexicographic objective without changing the intended search trajectory; the hash repair makes seeded behavior deterministic across Python hash seeds while preserving the historical CPython 3.12 / `PYTHONHASHSEED=0` trajectory.

The ordinary 8-case × 8-seed, 4,096-proposal gate after the hash repair produced:

- 2 better / 62 equal / 0 worse public objectives;
- relay count unchanged in all 64 runs;
- occupied area unchanged in all 64 runs;
- two wire-length improvements;
- proposal, acceptance, rejection, routing-work, and topology-rebuild counters exactly unchanged from pre-C;
- median runtime ratio 1.049× pre-C.

The later full budget/scale gate contained 101 baseline/current pairs:

- **6 better / 95 equal / 0 worse** public lexicographic objectives;
- relay count: 0 / 101 / 0 better/equal/worse;
- occupied area: 0 / 101 / 0;
- wire length: 6 / 95 / 0;
- proposal attempts and accepted moves: delta 0;
- noop, geometry, wire-reach, and Metropolis rejections: delta 0;
- routing priority-queue work: delta 0;
- topology rebuilds: delta 0;
- overall median runtime ratio: **1.031× pre-C**.

The opt-in 1,200-object sparse fixture remained equal in objective and had a runtime ratio of approximately 0.998×.

These measurements established a strong no-regression floor, but they did not solve the large-application density problem by themselves.

## Flat Snake baseline: the convergence failure

The first production Snake application check started from the complete `safe-folded-crossbar` routed seed and applied the flat generic annealer. The emitted blueprint behaved correctly in Factorio, but the 4,096-proposal seed-0 result remained extremely sparse:

```text
implementation combinators   602
relay combinators            2,482
occupied bounding-box area  91,805 tiles²
routed wire length          19,405.0
annealer runtime               480.0 s
physical occupancy             ~4.0%
```

Increasing the flat proposal budget from 512 to 4,096 reduced relays from 2,586 to 2,482 and area from 93,632 to 91,805, but did not materially change the global geometry. This established that the remaining problem was **move scale and representation**, not merely insufficient local proposals.

## Multilevel redesign

The successful C architecture separates global implementation placement from final relay routing.

### C2 — relay-blind logical hypergraph coarsening

Red/green electrical hypernets are reconstructed from the logical physical circuit without using current relay geometry or current physical distances. Deterministic heavy-edge matching produced the Snake hierarchy:

```text
613 -> 320 -> 173 -> 98 -> 58 -> 38 -> 27 macros
```

The 613 objects are 602 internal implementation combinators plus 11 fixed public markers. Snake contains 501 logical hyperedges; fixed markers stay singleton at every level.

### C3 — genuine coarse macro contraction

The coarsest hierarchy remains 27 actual placement objects rather than immediately legalizing all 613 singleton entities. Macro footprints derive from their implementation member area plus configurable packing slack. Fixed markers remain exact.

The resulting coarse Snake placement reduced the historical macro envelope to:

```text
area                 2438
width × height       46 × 53
implementation fill  49.38%
projected HPWL       1459.5
```

This established the correct global physical scale in seconds.

### C4 — coarse macro annealing

C4 optimizes only the coarse macro problem using local translations, affinity-directed cluster migration, swaps, small transactional moves, and bounded zoom pressure. Its cheap objective uses occupied macro area, logical-hypernet HPWL, and a congestion estimate.

At 8,192 proposals, seed 0:

```text
area                 2438 -> 2002
implementation fill  49.38% -> 60.14%
projected HPWL       1459.5 -> 1060.0
congestion           322.6 -> 145.6
```

A three-seed 16,384-proposal sweep kept every seed below the C3 starting area while producing projected HPWL values around 982–1114.

### C6 — nested hierarchical uncoarsening

The hierarchy is expanded level by level. Each finer macro belongs to one exact parent, and children subdivide the parent's **optimized region** rather than being respaced from historical singleton coordinates. Packing slack decreases toward 1.0 so the final singleton stage represents real Factorio footprints.

The complete `27 -> 38 -> 58 -> 98 -> 173 -> 320 -> 613` expansion preserved the compact coarse envelope:

```text
final singleton abstract area            1943.28
abstract implementation occupancy          61.96%
abstract logical HPWL                     2428.78
exact lattice-projected impl area            2070
exact projected implementation occupancy    58.16%
exact projected logical HPWL                  2622
```

Measured C6 hierarchy runtime was **93.84 s**.

### C5 — transactional fresh rerouting

After final singleton placement, the old failproof relay scaffold is discarded. C5 starts from zero relays, reconstructs the connector-aware logical nets, runs fresh deterministic feasible routing, simplifies the topology, and exact-validates the result. Any failure returns the exact validated original layout.

For the accepted Snake placement, fresh C5 routing took **12.19 s** and produced 174 relays.

## Final accepted Snake artifact

The seed-0 C3 -> C4 -> C6 -> fresh C5 result is:

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

The 11 fixed public markers occupy only a 31 × 1 tile envelope, confirming that anchor placement is not the source of the final bounding-box size.

The exact serialized blueprint was regenerated from merged main, imported into Factorio, and exercised successfully. It **behaved correctly and looked satisfactory in game**. This in-game result is the final application acceptance for Milestone C.

## Acceptance decision and the former 80% target

A strict >80% occupancy target was introduced when the flat Snake result was near 4%. It served its purpose: it prevented a correct-but-impractical sparse artifact from being treated as sufficient and forced the transition to a genuine multilevel architecture.

After the multilevel result reached 65.12%, the remaining gap was fine density optimization rather than a missing compiler capability. The current artifact is exact-valid, circuit-generic, bounded, relay-efficient, visually compact, and successful in the real game. The hard 80% threshold was therefore retired as a Milestone C exit requirement.

**65.12% is the accepted C reference result.** The 80% value remains an optional stretch target for future multilevel/global physical optimization.

## Rejected or deferred fine-compaction directions

Several experiments after the successful C6/C5 checkpoint are useful evidence but are not required for C completion:

- **Relay-first local C7:** relays 174 -> 149, area remained 2116, wire length worsened, and occupancy fell to 63.94%. The public relay-first objective can conflict with a density goal during fine search.
- **Global singleton zoom:** small legalization radii failed; larger radii legalized by scattering the 613 singleton objects, producing ~2550–2601 area and 401–443 relays. Global zoom is appropriate at macro scale, not as the current singleton legalizer.
- **Area-first fixed-topology C7:** area improved 2116 -> 2024 and relays 174 -> 152 after simplification, reaching 66.996% occupancy. This proved that post-route compaction can work, but the improvement is incremental and no longer needs to block the roadmap.
- **Bounded push-and-drag cluster refinement:** retained only as an experimental direction. If continued, it belongs to later physical-quality work rather than Milestone C acceptance.

## Final conclusion

Milestone C established a reusable physical optimization architecture rather than one successful Snake layout:

```text
validated fail-safe seed
    -> relay-blind logical hypergraph hierarchy
    -> compact coarse macro placement
    -> coarse net/congestion refinement
    -> nested hierarchical uncoarsening
    -> exact Factorio lattice projection
    -> fresh transactional routing from zero relays
    -> topology simplification
    -> exact validation and serialization
```

The next roadmap milestone is **Milestone D — Physical ABI completion and placement integration**. Further density work remains valuable, but it is now optional optimization unless a future component or application exposes a concrete need.
