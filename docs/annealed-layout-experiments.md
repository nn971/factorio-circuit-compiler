# Annealed joint-layout experiment ledger

The primary benchmark is `benchmarks.snake.generate`. Candidate comparisons use one layout retry,
5,000 annealing proposals, and the same fixed random seeds. Relay count remains the primary objective;
occupied area and routed wire length distinguish candidates without a relay-count regression.

## 2026-08-25 — rebased production baseline

- **Hypothesis:** establish the current same-budget frontier before changing the optimizer.
- **Change:** none (`73c8c76`, `agent/annealed-joint-layout-rebased`).
- **Budget:** 5,000 proposals, one retry, seeds 0–4.
- **Results:**

  | seed | relays | area | wire length |
  | ---: | ---: | ---: | ---: |
  | 0 | 1017 | 10062.0 | 8825.8 |
  | 1 | 1020 | 10179.0 | 8837.6 |
  | 2 | 1025 | 10062.0 | 8933.9 |
  | 3 | 1025 | 10179.0 | 8894.7 |
  | 4 | 1013 | 10062.0 | 8817.2 |

- **Conclusion:** relay count and wire length have improved beyond the older handoff baseline, but
  area remains about 10k. Automatic I/O markers stay at the initial `x=-2` / `x=114` perimeter;
  their incident reach constraints tether connected body entities to the initial width. Area
  compaction must allow the automatic interface perimeter to follow the compacted body while keeping
  explicit anchors exact and preserving ordered input/output columns.

## 2026-08-25 — progressive envelope and perimeter rebuild probes

- **Hypothesis:** shrink the whole feasible layout progressively, then rematerialize automatic I/O
  on the compacted perimeter and rebuild exact reach-safe topology outside the hot loop.
- **Change:** quadratic rectangular overflow pressure; epoch-local outlier sampling; automatic I/O
  may move during optimization, but only an exact ordered-perimeter artifact is returned.
- **Budget:** 5,000 proposals, one retry, seed 0 unless noted.
- **Results:**

  | variant | relays | area | wire length | conclusion |
  | --- | ---: | ---: | ---: | --- |
  | 70% envelope, all outliers | 1042 | 8715.0 | 9065.1 | area win, relay/wire regression |
  | 60/40 free/fixed phases | 1043 | 8715.0 | 9058.0 | dominated |
  | 40/60 free/fixed phases | 1068 | 8925.0 | 9170.5 | dominated |
  | 80/20 free/fixed phases | 1049 | 8925.0 | 9059.9 | dominated |
  | 70% envelope, implementation outliers first | 1023 | 8610.0 | 8844.6 | promising |
  | 60% envelope, implementation outliers first | 1007 | 8610.0 | 8786.9 | strict baseline win |
  | 50% envelope, implementation outliers first | 1013 | 8610.0 | 8777.1 | relay regression vs 60% |
  | 40% envelope, implementation outliers first | 1002 | 8610.0 | 8788.4 | best primary objective |
  | 30% envelope, implementation outliers first | 1013 | 8925.0 | 8770.3 | dominated on relay/area |

- **Conclusion:** implementation motion is what makes relay-chain segments bypassable; sampling
  relay outliers too eagerly weakens the primary objective. A 40% target is useful pressure even
  though hard reach prevents the body from attaining that literal scale in 5,000 proposals.

## 2026-08-25 — selected candidate, paired five-seed validation

- **Hypothesis:** discard stale annealed relays before choosing the new automatic-I/O perimeter, so
  relay outliers that will be rebuilt cannot hold the final envelope open.
- **Change:** first perimeter target uses implementation footprints only; subsequent bounded rebuild
  passes include the rebuilt relays and require the exact serialized artifact to reach a fixed point.
- **Budget:** 5,000 proposals, one retry, seeds 0–4; paired with the production baseline above.
- **Results:**

  | seed | baseline relays | candidate relays | baseline area | candidate area | baseline wire | candidate wire |
  | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
  | 0 | 1017 | 981 | 10062.0 | 7954.0 | 8825.8 | 8663.2 |
  | 1 | 1020 | 973 | 10179.0 | 8051.0 | 8837.6 | 8595.3 |
  | 2 | 1025 | 981 | 10062.0 | 7857.0 | 8933.9 | 8598.6 |
  | 3 | 1025 | 981 | 10179.0 | 8217.0 | 8894.7 | 8623.4 |
  | 4 | 1013 | 994 | 10062.0 | 8019.0 | 8817.2 | 8665.3 |

- **Conclusion:** strict Pareto improvement on every paired seed. Relay count improves by 19–47,
  occupied area by 19.3–21.9%, and routed wire length by 1.7–3.8%. The remaining gap to the ~5,000
  area stretch target is not caused by weak compactness pressure alone: 30–60% target scales all
  settle well above the literal target, implicating reach-preserving move mobility and the legal
  corridor/grid envelope as the next bottleneck.

## 2026-08-25 — coarse topology refresh schedule

- **Hypothesis:** even after local simplification, the explicit relay tree eventually tethers
  implementation entities to stale routes. Rebuilding shared-net topology at a few epoch boundaries
  should restore move mobility without adding work to ordinary proposals.
- **Change:** attempt reach-safe retopology outside the hot loop at 25%, 50%, and 75% of the proposal
  budget. A failed rebuild restores the previous relay positions/groups/topology exactly.
- **Budget:** 5,000 proposals, one retry, seeds 0–4; paired with both the production baseline and the
  progressive-envelope candidate above.
- **Results:**

  | seed | envelope-only relays | three-rebuild relays | envelope-only area | three-rebuild area | envelope-only wire | three-rebuild wire |
  | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
  | 0 | 981 | 956 | 7954.0 | 8019.0 | 8663.2 | 8502.5 |
  | 1 | 973 | 954 | 8051.0 | 8148.0 | 8595.3 | 8520.7 |
  | 2 | 981 | 970 | 7857.0 | 8316.0 | 8598.6 | 8590.0 |
  | 3 | 981 | 948 | 8217.0 | 8051.0 | 8623.4 | 8446.2 |
  | 4 | 994 | 980 | 8019.0 | 7790.0 | 8665.3 | 8555.7 |

- **Conclusion:** the three-rebuild schedule improves average relay count from 982.0 to 961.6 and
  wire length from 8629.2 to 8523.0, while average area moves only from 8019.6 to 8064.8. Against the
  same-budget production baseline it improves averages by 5.7% relays, 20.2% area, and 3.8% wire;
  every paired seed remains strictly better on all three metrics. The small area/relay trade within
  the improved frontier confirms that stale feasible topology was a material bottleneck.

## 2026-08-25 — Factorio acceptance

- **Hypothesis:** the promoted candidate's structural validation and physical simulation correspond
  to a working circuit after blueprint serialization and import into Factorio.
- **Change:** none; this is target-level acceptance of the selected artifact.
- **Budget:** 5,000 annealing proposals, one retry, seed 0.
- **Artifact:** `/tmp/snake-final-three-rebuild-seed0.txt`, generated with
  `uv run python -m benchmarks.snake.generate --annealing-layout --annealing-iterations 5000
  --corridor-width 4 --layout-retries 1 --census --output
  /tmp/snake-final-three-rebuild-seed0.txt`.
- **Result:** 956 relays, 8,019 tiles of bounding-box area, and 8,502.5 tiles of wire. The imported
  blueprint was tested in Factorio and the user reported that Snake behaves normally in game.
- **Conclusion:** the promoted general-purpose layout candidate passes the required end-to-end
  Factorio acceptance check.

## 2026-08-25 — channelized bootstrap and dual-color negotiated routing

- **Hypothesis:** the remaining area is inherited from the fallback bootstrap rather than from the
  annealing objective. The fallback reserves every other row and column, so the 602 implementation
  combinators occupy only about one quarter of an expanded grid. A denser connected routing lattice,
  plus the real two-color capacity of an empty constant-combinator relay, should make a half-baseline
  area feasible without weakening reach checks.
- **Changes:**
  - reserve one full routing column per four 2x1 columns and one routing row per eight rows, keeping
    every implementation terminal within the conservative seven-tile span while raising bootstrap
    density;
  - after bounded sequential allocation fails, negotiate cross-net congestion outside the proposal
    hot loop with local, bounded path expansion;
  - allow one physical relay constant to belong to at most one red and one green physical net; the
    routing plan still emits separate color-specific wires and never shares capacity between two
    same-color nets;
  - preserve the first pass's ordered automatic-I/O rows and adjust only the exact left/right
    perimeter on later passes, avoiding relay/interface vertical oscillation.
- **Dominated probes:**
  - channelized placement with the sequential router alone failed despite 34,440 free sites and
    reachable neighbors at both failed endpoints, confirming cross-net congestion rather than
    global capacity exhaustion;
  - eight failure-prioritized global reorder attempts still failed and added about a minute of work;
    this order-only experiment was reverted;
  - an unbounded all-neighbors negotiated search was stopped after more than two minutes. Restricting
    relay-to-relay expansion to local axis channels and 16 ranked candidates retained the feasible
    search while bounding coarse-router work.
- **Budget:** 5,000 proposals, one retry, seeds 0–4, paired with the original production baseline.
- **Results:**

  | seed | baseline relays | candidate relays | baseline area | candidate area | area reduction | baseline wire | candidate wire |
  | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
  | 0 | 1017 | 578 | 10062.0 | 4270.0 | 57.6% | 8825.8 | 6538.2 |
  | 1 | 1020 | 573 | 10179.0 | 4392.0 | 56.9% | 8837.6 | 6565.4 |
  | 2 | 1025 | 623 | 10062.0 | 4473.0 | 55.5% | 8933.9 | 6883.8 |
  | 3 | 1025 | 678 | 10179.0 | 4402.0 | 56.8% | 8894.7 | 7194.9 |
  | 4 | 1013 | 637 | 10062.0 | 4340.0 | 56.9% | 8817.2 | 7030.1 |

- **Conclusion:** every paired seed clears the 50% area-reduction target. Average area falls from
  10,108.8 to 4,375.4 (56.7%), average relays from 1,020.0 to 617.8 (39.4%), and average wire length
  from 8,861.8 to 6,842.5 (22.8%). Seed 0's exact acceptance artifact is
  `/tmp/snake-50pct-final-seed0.txt`; structural generation and the complete routine validation
  suite pass. The opt-in full framebuffer/reset semantic acceptance also passes. An independent
  check of the serialized seed-0 artifact found 1,191 entities, 578 relays (116 shared across one
  red and one green net), 1,476 wires, a maximum center span of exactly 7.0 tiles, no entity
  collisions, and distinct red/green connector ids on every shared relay. The exact artifact was
  imported into Factorio and the user confirmed that Snake behaves normally in game, completing
  target-level acceptance.

## 2026-08-25 — two-tile default corridor

- **Hypothesis:** the canonical two-tile corridor is sufficient for the channelized bootstrap and
  avoids making the Snake benchmark silently retain its historical four-tile override.
- **Change:** change `benchmarks.snake.generate --corridor-width` from 4.0 to 2.0 by default. The
  library `PlacementOptions` default was already 2.0.
- **Budget:** 5,000 proposals, one retry, seed 0.
- **Result:** 364 relays, 2,950 tiles of bounding-box area, and 4,631.7 tiles of wire. The run
  completed in 17.4 seconds and retained the seven-tile conservative reach limit.
- **Conclusion:** the two-tile default is a strict structural improvement over the accepted
  four-tile seed-0 artifact (578 relays, 4,270 area, 6,538.2 wire). The generated blueprint is
  `/tmp/snake-corridor2-final-seed0.txt`.
