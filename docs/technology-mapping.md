# Temporal technology mapping

This document defines the accepted architecture for target-aware temporal implementation choices.
The mapper is currently an opt-in path beside production `compile_circuit()`, but the complete
stateful Snake recurrence has been solved, physically lowered, synthesized, laid out, serialized, and
validated in Factorio.

## Architectural boundary

```text
canonical CircuitModule
    -> implementation-neutral MappingProblem
    -> joint temporal technology mapper
    -> RealizationPlan
    -> mapped AbstractPhysicalCircuit lowering
    -> signal allocation / red-green synthesis / layout
    -> Factorio blueprint
```

Semantic IR answers **what the circuit means**. The mapper answers **which Factorio mechanisms,
physical phases, and shared resources implement it**.

### Hard upstream rule

Implementation-dependent latency must not become an unconditional semantic constraint.

Upstream may contain logical occurrence order, structural clocks, externally prescribed period or
throughput constraints, and availability facts that hold independently of a chosen implementation. It
must not say that a semantic arithmetic operation, Select, or state cell intrinsically takes the
latency of today's ordinary Factorio realization.

Target latency first appears in implementation candidates or parameterized physical resources.

## MappingProblem

`mapping/problem.py` keeps target-neutral records:

```text
MappingSource
    semantic leaf + source availability/observation contract

MappingStateRead
    register + logical occurrence

MappingOperation
    semantic recipe + operand value ids

MappingSink
    required semantic value + physical demand phase

MappingStateTransition
    register + transition kind + logical occurrence + value/control ids
```

No concrete signal identity, wire color, entity coordinate, or route appears in the problem.

`build_stateless_level_mapping_problem()` extracts a stateless Level occurrence.

`build_periodic_level_mapping_problem()` remains a useful post-update/output-cone diagnostic where
state occurrences are external stable sources.

`build_periodic_state_mapping_problem()` is the full phase-neutral periodic recurrence. It traverses
both outputs and state-transition value/control cones while leaving state read/write phases unresolved.

## Finite implementation candidates

`mapping/templates.py` represents local finite implementation choices. A candidate owns:

```text
semantic operation covered
implementation kind
input phase offsets relative to output
entity cost
output availability mode
```

Ordinary arithmetic/compare/vector candidates use target latency from `FACTORIO_LATENCY`.

The current ordinary scalar Select candidate is a three-stage arithmetic mux:

```text
result = false + condition * (true - false)
```

with data required three ticks before output and condition two ticks before output. Dynamic false arms
may need internal exact preservation before the final add. That preservation is currently reported by
physical lowering as a known candidate-internal surcharge rather than optimized by CP-SAT.

The first non-ordinary finite candidate is a narrow zero-delay scalar wire sum. It deliberately has
strict single-use/non-nested restrictions until aggregation is modeled as a general shared resource.

## Periodic state candidates

State timing is target-owned, not imported from the production `StateTimingPlan` analyzer.

The accepted ordinary state candidates are:

```text
clocked Freeze
    local entities = 4
    data            = r - 1
    set when        = r - 2
    state read      = r

clocked one-add/one-clear Accumulator
    local entities = 4
    add data        = r - 1
    add when        = r - 2
    clear when      = r - 2
    state read      = r
```

Every periodic stateful plan also owns one shared three-entity commit resource:

```text
constant +1
modulo-P counter
startup-ready latch
```

The state cells fold clock/ready predicates directly into multi-condition deciders, so there is no
per-register clock entity.

See `docs/state-cell-mapping.md` for the concrete topologies and accepted Snake checkpoint.

## Joint solver

The stateless and periodic state solvers jointly choose physical phases and delivery mechanisms rather
than applying a fixed ASAP/ALAP schedule first.

For the periodic state+bus solver the objective is:

```text
selected operation entities
+ selected state-cell entities
+ shared periodic commit entities
+ private exact transport
+ shared delay-bus cost
```

A use may be:

```text
REUSE
    the same logical token is already valid at the consumer phase

OBSERVE_AT
    a live Level source may be observed freely at that phase

PRIVATE_TRANSPORT
    preserve the exact token through a private delay path

BUS_TRANSPORT
    preserve the exact scalar token on a selected shared delay bus
```

There is no global scheduling policy. ASAP, ALAP, and interior placements are all possible optima of
the same model.

## Shared scalar delay bus

The delay bus is the first parameterized shared-resource family. It is not enumerated as a finite
candidate because its benefit depends on an arbitrary subset of exact lifetimes.

One scalar lane is physically:

```text
producer
    -> signal-specific +0 ingress
    -> shared Each + 0 trunk
    -> signal-specific +0 egress per transported use
```

The current resource contract is:

- scalar exact lifetimes only;
- minimum lifetime length three ticks;
- at least two selected lanes per active bus;
- continuous middle span;
- one ingress per lane;
- one egress interface per transported semantic use;
- conservative capacity counts persistent lane identities;
- vector exact lifetimes stay private.

Bus membership, lane start/end, and middle span are variables in the same CP-SAT model as operation and
state-cell phases.

`tests/mapping/test_delay_bus_parity.py` checks the resource against the older fixed-placement transport
optimizer on controlled cases where their abstractions coincide.

## RealizationPlan

`mapping/plan.py` is the target-aware boundary immediately before mapped physical lowering. It records:

```text
SelectedRealization
SelectedStateCell
PeriodicCommitResource
PlannedDelivery
ExactLifetime
DelayBusResource / DelayBusLane
WireSumResource
```

It deliberately does not assign concrete Factorio virtual signals, wire colors, coordinates, or
routes.

Independent validators reconstruct candidate timing, state read windows, delivery classifications,
shared-resource legality, and objective costs from the plan.

## Mapped physical lowering

Two entry points exist:

```text
lower_stateless_mapping_plan()
lower_periodic_state_mapping_plan()
```

The periodic lowerer does not call `analyze_normalized_state_timing()`. It derives a backend-only state
timing adapter from the selected cell phases solely to reuse mature vector/read emission machinery.

The accepted stateful lowerer materializes:

```text
ordinary scalar/vector operation candidates
clocked Freeze and Accumulator cells
shared periodic commit
private exact transport
selected scalar delay buses
coherent Level framebuffer HOLD boundary
```

The lowering report distinguishes costs outside the current mapper objective:

```text
planned mapper cost
+ fixed semantic source hardware
+ candidate-internal Select preservation
+ output materialization
= accounted physical cost
```

`unexplained_gap == 0` is required before a mapped blueprint is trusted.

The coherent output HOLD is a boundary requirement rather than recurrence timing: an external lamp
screen observes its network continuously, while internal state cells may commit at different phases.
The hold captures one fully settled framebuffer per logical occurrence and keeps it stable between
captures.

## Accepted Snake result

The deterministic-food full recurrence at `P=60` is the current acceptance workload.

The joint solver proved:

```text
operation entities = 213
state entities     = 36
commit entities    = 3
transport           = 175
mapped objective    = 427
```

The corresponding all-private optimum is 605 with transport cost 353, so the selected bus saves 178
entities in the modeled recurrence.

The selected bus has 29 lanes, 74 middle stages over `[44, 118)`, and 70 interfaces.

Physical lowering accounts exactly for the known non-objective hardware:

```text
mapped objective               427
fixed source hardware            8
Select internal preservation    20
framebuffer HOLD                 2
                                ---
accepted implementation        457
unexplained gap                   0
```

The mapped blueprint was tested in Factorio and the Snake ran perfectly after the coherent framebuffer
HOLD was added. This validates the complete route from phase-neutral recurrence through joint mapping,
state cells, shared delay bus, physical synthesis, layout, and live external display.

### Reproduce

```bash
# Solve and compare with all-private transport.
uv run --with 'ortools>=9.14,<10' \
  python -m benchmarks.snake.analyze_mapping \
  --period 60 --solve-full-state --compare-private \
  --time-limit 300 --workers 8

# Inspect exact mapped physical accounting.
uv run --with 'ortools>=9.14,<10' \
  python -m benchmarks.snake.lower_mapping \
  --period 60 --time-limit 300 --workers 8

# Generate the in-game acceptance blueprint.
uv run --with 'ortools>=9.14,<10' \
  python -m benchmarks.snake.generate_mapping \
  --period 60 --time-limit 300 --workers 8
```

## Production status

The mapper remains separate from production `compile_circuit()` for this milestone. The production
route continues to be the compatibility/correctness baseline for ordinary users; the mapped route is
now a validated implementation path rather than an unfinished experiment.

A later integration milestone should decide how the mapper is selected, how optional OR-Tools support
is exposed, and whether legacy temporal/transport diagnostics remain public or become benchmark-only.

## Deferred optimization

The milestone intentionally stops after the validated delay-bus win. Candidate-internal Select
preservation (20 Snake entities) is known but small relative to the transport improvement and is not a
merge blocker.

Future work may include:

- explicit internal-port costing for Select and other compound candidates;
- additional state-cell shapes;
- n-way wire aggregation and other shared resources;
- fusion/rematerialization;
- reusable or time-multiplexed functional units;
- multi-clock/Event recurrence mapping;
- layout-cost feedback where abstract area alone is insufficient.
