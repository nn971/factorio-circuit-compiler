# Temporal settling and ALAP lowering milestone

Status: accepted and validated in Factorio on 2026-08-19.

This milestone changes the physical interpretation of periodic Level programs. A logical clock period
is no longer treated as a path length that every value must traverse with identity combinators. It is
a **settling budget** in which state-held and otherwise persistent values may remain on their existing
physical nets while combinational logic settles, and in which ordinary operations may be scheduled as
late as their consumers permit.

The heavyweight acceptance workload is the full interactive 16x16 Snake benchmark. The canonical
safe-folded blueprint produced after this work ran flawlessly in Factorio.

## Motivation

The pre-temporal Snake realization was functionally correct but dominated by phase-padding hardware:

```text
implementation combinators = 5,657
phase-delay combinators     = 4,960
state period                = 60 ticks
```

Most of those delays did not encode semantic history. They existed because the old lowerer eagerly
realized expressions at their earliest physical phase and then copied values through identity
combinators until all paths reached an exact common phase.

For a synchronous recurrence

```text
S[k+1] = F(S[k], I[k])
```

this is unnecessarily strict. Once semantic state is cut, the ordinary expression graph is a DAG.
State reads are held by their physical state elements between logical boundaries. If the inputs to a
consumer denote the same logical tokens when that consumer runs, unequal path lengths do not require
identity padding.

## Feedback-cut / settling principle

Let a periodic state domain have physical period `P`. Cutting the semantic state elements turns the
ordinary recurrence body into a feed-forward graph. A state read at physical phase `phi` represents
one logical state token throughout

```text
[phi, phi + P)
```

because the state element holds that value until the next logical boundary.

More generally, if one physical value is certified to denote the same logical token on `[a,b)`, a
consumer may use that net at any phase in the interval. An identity delay is required only when the
requested phase lies outside the certified interval or when the delay itself carries intentional
temporal meaning.

This yields the practical rule used by production lowering:

- held state and constants may remain physically early;
- combinational results inherit validity from their operands;
- exact delays remain the conservative fallback when persistence cannot be proved;
- startup/event timing and other intentional temporal guards remain exact;
- outputs that are observed continuously are synchronized at the observation boundary rather than by
  forcing all internal paths to remain phase-aligned.

## Experimental path that established the design

Before changing production lowering, several isolated diagnostic/probe paths were added under
`factorio_circuit.experimental` and `benchmarks/snake`:

- temporal-hypergraph inspection;
- delay-reuse projection;
- an executable delay-bypass lowering;
- benchmark scripts for the full and no-framebuffer Snake variants.

The projection suggested that almost all ordinary phase padding in the simple synchronous Snake
feedback structure was removable. The executable bypass probe gave the decisive behavioral result:
Snake gameplay still worked when most delays were bypassed, while framebuffer pixels were visibly
unsynchronized. This separated two concerns that the previous implementation conflated:

1. internal recurrence correctness, which needs settling before the next state boundary; and
2. externally dense observation, which needs a coherent output materialization policy.

The experimental package remains diagnostic and is not imported by the canonical production pipeline.

## Production validity-window settling

`factorio_circuit.lowering.settling.SettlingVectorLowerer` carries a conservative validity window for
each realized scalar/vector Level value.

Current important cases are:

- constants: unbounded validity;
- periodic state reads: one complete state period;
- raw external Level snapshots: one physical tick;
- vector lane reads: inherit the source-vector proof;
- ordinary arithmetic/comparison/vector operations: inherit the aligned intersection/minimum span of
  operand validity.

When `delay_to(...)` or `delay_vector_to(...)` requests a later phase, the lowerer reuses the existing
net if that phase lies inside the certified window. Otherwise it falls back to the old exact delay
chain. Missing proof therefore never removes required hardware.

Startup readiness is explicitly forced through exact delay lowering. A constant `1` is numerically
stable, but the startup chain denotes a temporal guard and must not be collapsed merely because its
payload is stable.

### Output HOLD

Level outputs default to `HOLD`. If a value naturally remains valid throughout the full output period,
HOLD is free. Otherwise the lowerer inserts a compact periodic capture/feedback cell at the observation
boundary. This fixed the framebuffer behavior exposed by the blind bypass experiment without restoring
internal phase padding.

## Shared exact-delay trunks

Scalar exact-delay transport already shared prefixes. Whole-vector transport originally did not, so
`SharedVectorDelayLowerer` added the same physical-prefix cache for vectors.

This is a correctness-preserving local optimization, but the full Snake census was unchanged:

```text
phase-delay.vector = 1,934 before trunk sharing
phase-delay.vector = 1,934 after trunk sharing
```

That negative result was useful. It showed that the remaining delays were not mostly duplicate copies
of one physical value. The computation had already branched into distinct intermediate nets before the
identity chains were emitted.

## ALAP scheduling

The dominant remaining pathology was eager ASAP lowering. For a fresh snapshot `x` feeding many cheap
operations,

```text
f(x), g(x), h(x), ...
```

ASAP realization computed the cheap functions immediately and then delayed every distinct result to a
late state boundary. Prefix sharing could not help because the delay chains had different source nets.

`factorio_circuit.lowering.alap.AlapVectorLowerer` therefore builds a backward deadline schedule from
periodic state-transition input phases. A one-tick operation whose result is required at phase `T` is
placed with inputs at `T-1` when legal. Shared semantic nodes receive the earliest deadline required by
all of their consumers.

Crucially, ALAP does **not** change the semantics of an external snapshot. It moves computation later,
not sampling later. If a fresh external token must survive until a late operation, exact transport is
pushed upstream toward that shared token. The transport can then be shared before the cheap functions
fan out.

Vector lane reads receive special treatment: when several scalar lanes are taken from one external
vector, the whole vector snapshot is transported first and individual lanes are projected afterwards.
This avoids turning one shared vector source into several scalar delay chains.

The initial production ALAP scope is deliberately conservative:

- periodic state-transition cones receive deadlines;
- shared nodes use the earliest consumer deadline;
- missing deadlines keep the previous ASAP realization;
- some packing paths retain their established schedule;
- output-only cones are not yet globally scheduled backward from their observation boundary.

## Quantitative result

The progression on the canonical full Snake workload was:

```text
                           implementation   phase delays
pre-temporal/main              5,657           4,960
validity settling              3,563           2,862
settling + ALAP                1,131             430
```

The final abstract-physical census is:

```text
implementation entities = 1,131
annotation entities     =    11
abstract entities total = 1,142
abstract nets           = 1,006

computation             =   453
state implementation    =   222
phase delays            =   430
  scalar                =   406
  vector                =    24
constants               =    26

state period            = 60 ticks
```

Relative to the previous validated `dense-safe-folded-v1` implementation count, this is an
approximately **80.0% reduction** in implementation combinators without changing the Snake algorithm
or state period. Relative to the original 4,960 phase delays, approximately **91.3%** of phase-padding
combinators were removed.

The computation count remained 453 through the ALAP step, and the state implementation remained 222.
The reduction therefore came from temporal transport rather than deleting game logic.

## Residual delay census

A structural `phase_delay_census` diagnostic was added to reconstruct the exact-delay graph after ALAP.
The accepted full Snake result is:

```text
total delays        = 430
scalar              = 406
vector              = 24
components          = 32
linear components   = 32
branching           = 0
merging             = 0
max component size  = 80
max depth           = 80
```

Delay-weighted sources:

```text
computation     270
clock/startup    80
external input   80
```

The two external-input trunks are one 56-tick scalar chain from `reset` and one 24-tick vector chain
from `movement`. The largest remaining component is the intentional 80-tick startup-ready chain used
by output HOLD. The remaining computation-sourced chains are mostly in the post-state framebuffer /
output computation, which is outside the first ALAP root set.

The absence of branching/merging components is important: there is no longer a hidden forest of
unshared delay trunks. Remaining optimization is now localized and understandable.

Use:

```bash
uv run python -m benchmarks.snake.census --deep-delays
```

for the structural residual report.

## Factorio acceptance

The canonical `safe-folded-crossbar` Snake generated from the settling + ALAP production pipeline was
validated manually in Factorio and reported to run flawlessly. This is the acceptance criterion for
the milestone.

The previous simulated-annealing/net-aware placer was also retried after the circuit shrank to about
1.1k implementation combinators, but it still struggled. This is now treated as an independent placer
scalability problem rather than a temporal-lowering blocker. The safe-folded layout remains the
canonical reliable strategy.

The append-only numeric acceptance record is in `benchmarks/snake/baselines.json` as
`settling-alap-v1`.

## What this milestone establishes

The compiler now treats periodic Level timing in the intended way:

```text
logical clock period = deadline / settling budget
                     != mandatory identity-delay path length
```

For ordinary synchronous feedback, state values can remain held while combinational paths settle.
When a computation is needed late, the compiler can move the computation toward its demand rather
than eagerly compute and transport every derived value through the entire period.

The Snake result demonstrates both principles at a scale large enough to expose path-alignment
pathologies that small unit circuits do not reveal.

## Deferred work

The following items are intentionally left for later rather than extending this optimization pass:

- ALAP rooted at Level output/HOLD observation boundaries;
- compact realization of startup/clock readiness;
- explicit fast/slow periodic clock domains and rate crossings;
- alternative input-sampling policies when the program intentionally wants a different occurrence
  cadence rather than one coherent slow-domain snapshot;
- further Snake-specific renderer/state simplification;
- scalability redesign for the simulated-annealing/net-aware placer.

These are no longer required to justify the current temporal model. They can be revisited when a new
semantic capability or benchmark exposes a concrete need.

## Relevant production files

```text
src/factorio_circuit/lowering/settling.py
src/factorio_circuit/lowering/alap.py
src/factorio_circuit/lowering/vector_delay_trunks.py
src/factorio_circuit/lowering/open_vector_pipeline.py
src/factorio_circuit/analysis/phase_delay_census.py
src/factorio_circuit/analysis/physical_census.py

tests/timing/test_level_settling.py
tests/timing/test_alap_lowering.py
tests/analysis/test_phase_delay_census.py

benchmarks/snake/census.py
benchmarks/snake/baselines.json
```
