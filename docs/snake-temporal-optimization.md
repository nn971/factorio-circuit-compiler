# Snake temporal placement and delay buses

Snake is the stress benchmark for the next physical-timing optimization milestone. The accepted
starting point is the randomized-food Snake with ALAP external observation: 698 implementation
combinators at physical state period 65. Its abstract-physical census contains 421 phase-delay
combinators, of which 403 are scalar and only 18 are whole-vector delays.

The goal of this branch is to stop treating ASAP or ALAP as a global scheduling policy. Instead,
computation placement and transport sharing are optimized jointly over a temporal computation
hypergraph.

## Temporal hypergraph

The first model is built before phase-delay combinators exist. Periodic state-transition inputs are
fixed sinks. Scalar/vector computations are nodes with an integer output phase, and a produced value
with multiple consumers is represented by one fanout lifetime rather than unrelated point-to-point
delay edges.

Existing timing analysis supplies the recurrence period and transition-input phases. Forward timing
gives each computation its earliest legal phase; reverse constraints give its latest legal phase.
ASAP and ALAP are therefore just the two extremal feasible placements inside these mobility
windows.

A `VectorSignal` lane read is modeled as a zero-latency electrical view of its underlying vector,
not as a computation node.

## Observation and settling classes

The model distinguishes three source behaviors inside one periodic reaction:

- `STABLE`: constants and held state reads. Computations derived entirely from stable sources follow
  those held values continuously and do not require scalar identity transport inside the modeled
  occurrence.
- `LIVE`: phase-zero external Level inputs/oracles under `SamplingPolicy.ALAP`. Ordinary ALAP
  lowering permits a later consumer to observe such a source directly. The temporal optimizer adds
  a stronger coherence rule: all direct uses in one logical occurrence share one observation tick,
  chosen as late as possible (the earliest direct-consumer input). Uses after that tick transport
  the exact captured token instead of independently resampling the external wire.
- `EXACT`: explicit/nonzero logical samples and beginning-of-step observations. They denote a
  particular physical tick and must be transported when used later.

The coherent-LIVE rule is necessary for multi-use external values. Without it, separate direction
comparisons can see different movement-detector states in one logical occurrence, and the random-food
provider can validate one selector result while gating a later, different result. The first physical
temporal Snake exposed exactly this failure in Factorio.

A computation whose ancestry contains a LIVE or EXACT source is phase-specific under the current
one-realization model. Those scalar results are the first delay-bus candidates.

## Continuous scalar delay bus

A delay bus is a physical series of:

```text
Each + 0 -> Each
```

stages. Compatible scalar values occupy distinct abstract signal lanes on the same bus. A lane may
join after the bus begins; once present, it is allowed to continue through later bus stages even
after its final consumer because its signal identity is distinct from the other lanes.

For assigned lifetimes `[s_i, e_i)`, the exact solver currently prices one continuous bus as:

```text
max(e_i) - min(s_i)
```

with a finite lane capacity. This is deliberately more conservative than pretending arbitrary lanes
can disappear from an `Each` pipeline for free. The default compiler scratch palette currently has
65 base-game virtual signals, deliberately disjoint from the framebuffer's fixed virtual pixel ABI.
A compatibility hook can forbid pairs from sharing a bus; deriving those incompatibilities
automatically from physical-lane interference is a follow-up step.

The experimental lowerer realizes a solved bus directly in abstract physical IR. Independent scalar
producer nets may join one bus stage, later stages carry all active lanes with one `Each` arithmetic
combinator, and consumers tap the appropriate stage using their own abstract signal identity.

## Exact fixed-period search

The optional proof backend uses OR-Tools CP-SAT. OR-Tools is intentionally not a project dependency
or part of `uv.lock`; supply it only for the diagnostic invocation:

```bash
uv run --with 'ortools>=9.14,<10' \
  python -m benchmarks.snake.temporal_optimize --solve \
  --time-limit 120
```

Without the optional solver, the diagnostic still prints the current physical census plus temporal
hypergraph/mobility information:

```bash
uv run python -m benchmarks.snake.temporal_optimize
```

The solved diagnostic closes the current loop:

```text
semantic state cone
    -> temporal hypergraph
    -> exact phase + coherent live-source + scalar-bus search
    -> planned abstract-physical lowering
    -> realized entity/delay census
```

A CP-SAT status of `OPTIMAL` means optimal only for the explicitly modeled fixed-period state-cone
problem, not yet for the complete compiled Snake.

## First physical candidate: rejected by gameplay

On 2026-08-20 the first 30-second, 8-worker solve completed the entire
semantic -> CP-SAT -> temporal-plan -> signal-allocation -> safe-folded-layout path at period 65. A
representative realized abstract physical census was:

```text
baseline ALAP                     temporal plan
implementation     698            454
phase delays       421            165
  private scalar   403             94
  scalar bus         0             54
  vector             18             17
state impl           60             60
max lanes/net         1          25-36 (solver-dependent)
```

The solver candidate had objective 71 and a best bound in the mid-60s, using three scalar buses. The
physical circuit therefore demonstrated the intended packing effect: 244 fewer implementation
combinators (35.0%) and 256 fewer phase-delay combinators (60.8%) without increasing the state
period. Signal allocation and layout also succeeded; one generated layout had 16,181 relays and a
458x436-tile extent.

However, Factorio gameplay rejected this candidate. The snake could bump incorrectly while turning,
eaten food could remain visible, the first food could appear late, and frames looked uneven. The
root semantic flaw was that LIVE sources were priced and lowered as independently resampleable at
every consumer phase. That is not coherent for one logical occurrence, particularly for the movement
vector and the nondeterministic Random Input selector.

The solver and temporal lowerer now model one coherent observation tick for each LIVE source. The
next generated candidate must pass gameplay again before any temporal result becomes an accepted
milestone. The 698-combinator random-food ALAP build remains the correctness baseline.

Generate the current candidate blueprint with the dedicated experimental runner:

```bash
uv run --with 'ortools>=9.14,<10' \
  python -m benchmarks.snake.generate_temporal \
  --time-limit 30 \
  --workers 8 \
  --census \
  --output snake-temporal-blueprint.txt
```

The optimization report now prints the coherent observation phase and latest direct use for each
LIVE source. The ordinary `benchmarks.snake.generate` command intentionally remains on the accepted
ALAP path until a temporal candidate passes the in-game acceptance checklist.

## Deliberate first-milestone boundaries

The first search keeps these decisions fixed or outside the objective:

- state period remains the inferred period (65 for the accepted Snake baseline);
- one semantic computation has one physical realization;
- arithmetic packing, compare/select fusion, and rematerialization are disabled in the planned
  lowering path;
- whole-vector delay buses are not modeled;
- output-only computation cones and output materialization are not yet global-search sinks;
- periodic-clock startup transport is not part of the hypergraph objective;
- LIVE provider/input observations are coherent, but their provider-internal implementation choices
  are not optimized;
- physical lane incompatibility is currently an explicit solver input rather than derived
  automatically.

These boundaries make the first number interpretable. After physical/in-game validation, the next
steps are to derive bus compatibility from physical IR, add output/startup sinks, then search the
period-slack Pareto frontier (`P`, `P+1`, ...). Only after that should computation
cloning/rematerialization become another solver choice.
