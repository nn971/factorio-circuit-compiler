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
- `LIVE`: phase-zero external Level inputs/oracles under `SamplingPolicy.ALAP`. They may be observed
  directly at a later consumer phase without a delay chain.
- `EXACT`: explicit/nonzero logical samples and beginning-of-step observations. They denote a
  particular physical tick and must be transported when used later.

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
can disappear from an `Each` pipeline for free. The default capacity is the size of the compiler's
stable virtual-signal allocation pool (currently 51 lanes). A compatibility hook can forbid pairs
from sharing a bus; deriving those incompatibilities automatically from physical-lane interference
is a follow-up step.

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
    -> exact phase + scalar-bus search
    -> planned abstract-physical lowering
    -> realized entity/delay census
```

A CP-SAT status of `OPTIMAL` means optimal only for the explicitly modeled fixed-period state-cone
problem, not yet for the complete compiled Snake.

## Staged fixed-period success

On 2026-08-20 a 30-second, 8-worker solve successfully completed the entire
semantic -> CP-SAT -> temporal-plan abstract-lowering path at the unchanged period 65. The realized
abstract physical census was:

```text
baseline ALAP                     temporal plan
implementation     698            454
phase delays       421            165
  private scalar   403             94
  scalar bus         0             54
  vector             18             17
state impl           60             60
max lanes/net         1             32
```

The solver candidate had objective 71 and best bound 64: 54 scalar bus stages plus 17 vector delays,
using three scalar buses. The realized circuit therefore reduced implementation combinators by 244
(35.0%) and phase-delay combinators by 256 (60.8%) without increasing the state period.

This is deliberately recorded as `temporal-delay-bus-prevalidation-v1`, not yet as an accepted
in-game milestone. The next acceptance gate is concrete signal allocation, safe-folded layout,
blueprint import, and full Factorio gameplay validation.

Generate that candidate blueprint with the dedicated experimental runner:

```bash
uv run --with 'ortools>=9.14,<10' \
  python -m benchmarks.snake.generate_temporal \
  --time-limit 30 \
  --workers 8 \
  --census \
  --output snake-temporal-blueprint.txt
```

The ordinary `benchmarks.snake.generate` command intentionally remains on the accepted ALAP path
until this candidate passes the in-game acceptance checklist.

## Deliberate first-milestone boundaries

The first search keeps these decisions fixed or outside the objective:

- state period remains the inferred period (65 for the accepted Snake baseline);
- one semantic computation has one physical realization;
- arithmetic packing, compare/select fusion, and rematerialization are disabled in the planned
  lowering path;
- whole-vector delay buses are not modeled;
- output-only computation cones and output materialization are not yet global-search sinks;
- periodic-clock startup transport is not part of the hypergraph objective;
- provider timing beyond ordinary Level source behavior is not yet optimized;
- physical lane incompatibility is currently an explicit solver input rather than derived
  automatically.

These boundaries make the first number interpretable. After physical/in-game validation, the next
steps are to derive bus compatibility from physical IR, add output/startup sinks, then search the
period-slack Pareto frontier (`P`, `P+1`, ...). Only after that should computation
cloning/rematerialization become another solver choice.
