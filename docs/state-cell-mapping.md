# Periodic state-cell technology mapping

This note records the accepted stateful temporal-technology-mapping milestone. The mapper keeps
physical latency out of canonical recurrence semantics, chooses state-cell phases and scalar shared
transport in one CP-SAT model, lowers the selected `RealizationPlan` to Abstract Physical IR, and has
been validated end-to-end with the full Snake benchmark in Factorio.

The mapped path is still opt-in and separate from production `compile_circuit()`.

## Semantic boundary

The canonical recurrence problem contains no physical register ABI:

```text
MappingStateRead
    register + logical occurrence

MappingStateTransition
    register + kind + logical occurrence + semantic data/control ids
```

A prescribed `MappingProblem.period` is an occurrence/throughput constraint. It is not a register
phase. The selected target candidates own physical read/write timing.

For a selected state cell with base read phase `b` and logical period `P`, state occurrence `k` is
stable on:

```text
[b + kP, b + (k+1)P)
```

Uses inside that interval are free `REUSE`. Exact preservation begins only after the final free phase.

## Shared periodic commit resource

Periodic state updates use one shared three-entity resource per mapped recurrence:

```text
constant +1
    -> modulo-P arithmetic counter
    -> self-latching startup-ready decider
```

The clock and ready networks are reused by every mapped state cell. This resource is explicit in
`RealizationPlan.periodic_commit` and contributes three entities to the mapper objective.

The startup-ready guard prevents an incompletely initialized recurrence from committing during the
first physical cycle.

## Clocked Freeze candidate

The accepted Freeze implementation costs four local entities:

```text
set condition + clock/ready -> pass decider ----\
                                                   -> data gate -> memory network
set inactive / not-boundary -> hold decider ----/                 |
                                                                    -> vector memory
```

If `S[k+1]` becomes visible at phase `r`, its semantic target ports are:

```text
data      = r - 1
set when  = r - 2
state     = r
```

The two deciders fold the shared clock-boundary and startup-ready predicates directly into their
conditions, so no per-register clock combinator is required.

## Clocked Accumulator add+clear candidate

The currently supported Accumulator shape is exactly one conditional add plus one clear, which is the
shape used by Snake. It also costs four local entities:

```text
add when + !clear + clock/ready -> add-active decider
!clear or !ready or !boundary   -> retain decider
add data * add-active           -> gated vector add
memory * retain                 -> vector memory
```

Relative to the next state read phase `r`:

```text
add data   = r - 1
add when   = r - 2
clear when = r - 2
state      = r
```

This replaces the earlier six-entity prototype. Factorio multi-condition deciders absorb the
normalization/control logic that previously required separate combinators and one-tick preservation.

Other accumulator shapes remain unsupported rather than being assigned guessed costs or timing.

## Stateful solvers

`solve_periodic_state_mapping_problem()` is the all-private baseline. It jointly chooses operation
candidates/phases, one state-cell candidate/base phase per register, transition port phases, stable
state-read reuse, source observation/reuse, and exact private transport.

`solve_periodic_state_bus_mapping_problem()` adds the scalar delay-bus resource to the same solve. It
jointly chooses:

```text
operation phases
state-cell base phases
transition port phases
exact lifetime lengths
private versus bus transport
bus lane membership
bus middle span
```

The objective is:

```text
operation candidate entities
+ state-cell entities
+ shared periodic commit entities
+ private exact transport
+ bus middle stages
+ bus ingress/use interfaces
```

The current bus remains deliberately narrow:

- scalar exact lifetimes only;
- one producer lifetime is either private or wholly assigned to one bus;
- a bus lane requires at least three ticks of exact lifetime;
- an active bus contains at least two lanes;
- one isolated egress is charged per transported semantic use;
- vector state-read lifetimes remain private.

The validator independently reconstructs state timing and costs, then validates the selected delay
bus against the same isolation/resource rules used by stateless mapping.

## Mapped physical lowering

`lower_periodic_state_mapping_plan()` lowers the selected recurrence without calling
`analyze_normalized_state_timing()`. A small backend-only `StateTimingPlan` adapter is derived from the
selected state-cell phases solely to reuse mature vector/read emission code; it is not an upstream
timing analysis result.

The lowerer materializes:

```text
ordinary scalar/vector operations
shared periodic commit resource
clocked Freeze/Accumulator cells
private exact transport
selected scalar delay bus
coherent Level framebuffer HOLD boundary
```

The lowering report separates costs not yet represented in the CP-SAT objective:

```text
planned mapper cost
+ fixed semantic source hardware
+ candidate-internal Select preservation
+ output materialization
= accounted physical cost
```

`unexplained_gap == 0` is the structural checkpoint.

The framebuffer `HOLD` boundary is important even when the recurrence itself is correct at its sampled
output phase: physical displays observe circuit networks continuously. The two-decider hold cell
captures a coherent frame once per period and hides transient mixed frames caused by independently
phased internal state cells.

## Accepted Snake checkpoint

The default deterministic-food one-step Snake at `P=60` has nine mapped registers:

```text
6 Freeze      * 4 = 24
3 Accumulator * 4 = 12
shared commit     =  3
                    --
state + commit      39
```

The joint state+bus solver proved this plan optimal:

```text
operation entities = 213
state entities     = 36
commit entities    = 3
transport           = 175
mapped objective    = 427
```

The all-private comparison was:

```text
transport = 353
total     = 605
```

so the shared bus saves 178 entities in the modeled recurrence. The selected bus has:

```text
29 scalar lanes
middle [44, 118)
74 shared middle stages
70 interfaces
```

Mapped physical lowering before the output HOLD produced exactly:

```text
planned mapper cost          427
fixed source entities          8
Select-internal preservation  20
                              ---
implementation                455
unexplained gap                 0
```

The coherent framebuffer HOLD adds two implementation combinators, giving the accepted mapped
implementation **457 combinators**. The recurrence plan itself remains 427.

The selected state phases need not be uniform. In the accepted optimum, `head_x` and `head_y` used base
phase 42 while the other seven cells used phase 43. The in-game acceptance therefore also validates
that independent state-cell phasing is compatible with a coherent public output boundary.

The generated blueprint was tested in Factorio after adding the framebuffer HOLD and the Snake ran
perfectly: movement, state updates, body rendering, and head/body synchronization were correct.

### Reproduce the solve

```bash
uv run --with 'ortools>=9.14,<10' \
  python -m benchmarks.snake.analyze_mapping \
  --period 60 \
  --solve-full-state \
  --compare-private \
  --time-limit 300 \
  --workers 8
```

### Check mapped physical accounting

```bash
uv run --with 'ortools>=9.14,<10' \
  python -m benchmarks.snake.lower_mapping \
  --period 60 \
  --time-limit 300 \
  --workers 8
```

### Generate the accepted mapped blueprint

```bash
uv run --with 'ortools>=9.14,<10' \
  python -m benchmarks.snake.generate_mapping \
  --period 60 \
  --time-limit 300 \
  --workers 8
```

## Deferred work

The milestone intentionally stops here. Useful future work includes making the currently unpriced
Select-internal preservation explicit in candidate costs, adding more state-cell families, extending
shared resources beyond scalar delay buses, and eventually deciding when the mapped route should
replace or integrate with production `compile_circuit()`.
