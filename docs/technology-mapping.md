# Temporal technology mapping

This document defines the architectural boundary for target-aware temporal implementation choices.
The new mapper remains separate from the canonical compiler route while it is validated against the
established Level/Snake lowering path.

## Boundary

```text
canonical CircuitModule
    -> implementation-neutral mapping problem
    -> joint temporal technology mapping
    -> RealizationPlan
    -> deterministic mapped lowering
    -> AbstractPhysicalCircuit
    -> signal/wire/layout synthesis
```

Semantic IR answers **what the circuit means**. The mapper answers **which Factorio mechanisms,
physical phases, and shared resources implement it**. Abstract Physical IR records chosen target
topology while still leaving concrete signal identities, red/green assignment, placement, and
routing unresolved.

### Hard upstream rule

No implementation-dependent latency may become an unconditional mapping constraint.

Upstream information may contain logical occurrence order, structural clocks, externally prescribed
throughput/period constraints, and source contracts that are true independently of a chosen target
implementation. It may not assert that a semantic `BinaryOp`, `Select`, state register, or other
recipe necessarily has the latency/phase of the current ordinary lowerer.

Candidate timing is conditional on implementation selection. For example:

```text
ordinary arithmetic     requires operands at p-1, produces at p
zero-delay wire sum     requires contributors at p, produces at p
future fused candidate  may eliminate an intermediate physical value
future state cell       owns its read/write port phases relative to occurrences
```

## Mapping problem

`mapping/problem.py` contains implementation-neutral records:

```text
MappingSource
    fixed semantic leaf + externally valid availability contract

MappingStateRead
    register identity + logical occurrence; physical read phase unresolved

MappingOperation
    semantic recipe + operand value ids

MappingSink
    externally fixed demand for one semantic value

MappingStateTransition
    register/kind/occurrence + value/control ids; physical write phase unresolved

MappingUse
    physically timed producer/consumer relation for the currently supported solver subset
```

`MappingSource.semantic`, `MappingStateRead.semantic`, `MappingOperation.semantic`, and
`MappingStateTransition.semantic` preserve canonical-IR provenance. No concrete Factorio signal,
wire color, placement, or route appears here.

### Stateless extraction

`build_stateless_level_mapping_problem` handles one stateless Level occurrence with caller-supplied
output phases. Target operation latency is not inferred during extraction; ordinary latency first
appears when implementation candidates are generated.

### Periodic output-cone diagnostic

`build_periodic_level_mapping_problem` is deliberately a **boundary abstraction**, not the final
recurrence IR. The caller supplies a logical period `P`. A register occurrence at logical offset `k`
is externalized as a stable source on

```text
[kP, (k+1)P)
```

and the extractor walks only values reachable from module outputs. State-transition value/control
cones are not pulled in merely because they exist.

This is useful for questions such as:

> Given an already prescribed logical cadence, how should the post-update rendering/output cone be
> scheduled and transported?

It is especially useful for Snake's outputs after `Circuit.step(1)`. It does **not** claim that every
possible physical state-cell implementation exposes its read port exactly at `kP`; that assumption is
only part of this diagnostic boundary abstraction.

### Full periodic recurrence extraction

`build_periodic_state_mapping_problem` is the phase-neutral recurrence representation. It traverses
both module outputs and every canonical periodic `StateTransition` value/control cone.

Register occurrences become `MappingStateRead` records containing register identity and logical
offset but **no** `start_phase` or availability window. State transitions become
`MappingStateTransition` records containing their semantic update obligation but **no** physical
consume/write phase.

Therefore this full problem does not import any of:

```text
StateTimingPlan.state_phase
StateTimingPlan.transition_input_phase
ordinary state-lowering combinator depth
```

A future state-cell candidate must provide those physical port timing equations. Until that exists,
`MappingProblem.uses()` intentionally rejects a problem containing unresolved state reads or state
transitions. The current joint solver consequently fails loudly instead of silently inventing a state
ABI.

The first periodic extractors still require non-negative sampled-input offsets when they must map an
external observation onto a non-negative physical horizon. Phase-neutral `MappingStateRead` itself
preserves the canonical logical offset without assigning a physical phase.

## Finite implementation candidates

`mapping/templates.py` owns finite local alternatives. A candidate currently records:

```text
semantic operation covered
implementation kind
input phase offsets relative to output
abstract implementation entity cost
output availability mode
```

Ordinary candidates deliberately own `FACTORIO_LATENCY`. This is the boundary test: target latency
first appears when an implementation alternative is introduced, not while semantic causality is
extracted.

Exactly one finite candidate is currently selected for each mapped semantic operation. This is a
milestone restriction; fusion/rematerialization will eventually allow one candidate to cover several
semantic recipes or multiple physical realizations of one semantic recipe.

## Joint solver

`mapping/solver.py` jointly chooses finite candidates, physical phases, exact lifetimes, and optional
shared delay-bus membership. With buses disabled, the objective is:

```text
selected implementation entity cost
+ prefix-shared private exact-lifetime length
```

Sources may be `STABLE`, `OBSERVABLE`, or `EXACT`. A free source use remains a delivery decision in
the same solve; only the residual interval after its last free phase becomes exact transport.

There is no global ASAP or ALAP policy. ASAP, ALAP, and interior placements are all possible optima of
the same model.

Full stateful mapping is intentionally not accepted by the current solver. Its read/write phases are
unresolved until state-cell candidates are introduced.

## Joint delay-bus resource

`solve_mapping_problem(..., max_delay_buses=N)` enables the first parameterized shared-resource
family. Unlike the established fixed-placement transport optimizer, bus membership and bus span are
variables in the same solve as computation phases.

For one selected scalar exact lifetime:

```text
semantic producer
    -> signal-specific +0 ingress          one tick
    -> bus-private Each + 0 -> Each trunk  shared middle
    -> signal-specific +0 egress           one per transported semantic use
```

A lane may join a bus only when its exact lifetime has length at least three ticks. A selected bus has
at least two lanes. Its continuous middle is

```text
[min(lane.start + 1), max(lane.end - 1))
```

and its charged cost is

```text
continuous middle stages
+ one ingress per lane
+ one isolated interface per transported semantic use
```

`max_delay_buses=0` remains the default, preserving prior mapper behavior unless shared buses are
explicitly enabled. `delay_bus_capacity` conservatively bounds persistent scalar lanes on a bus.

### Fixed-placement parity checkpoint

`tests/mapping/test_delay_bus_parity.py` projects controlled fixed exact lifetimes into both:

```text
new joint mapper
established analysis/transport_optimize.py model
```

For cases with unique physical tap phases, they must agree on objective combinator cost, bus/private
partition, middle span, and assigned producer lanes. The tests include a short lifetime that remains
private and a staggered-start case.

One difference is currently deliberate. The old fixed-placement optimizer coalesces multiple
consumers on the same physical tap phase; the joint mapper charges/lowers one isolated interface per
semantic use. A dedicated regression records this mismatch. Equal-phase egress coalescing should be
added later as an explicit sharing mechanism rather than hidden in extraction.

## Realization plan

`mapping/plan.py` is the target-aware boundary immediately before mapped physical lowering:

```text
SelectedRealization
    candidate + output phase

PlannedDelivery
    reuse / observe-at / private exact transport / bus exact transport

ExactLifetime
    semantic exact-token lifetime before transport realization

WireSumResource
    intentional same-carrier aggregation network

DelayBusResource
    continuous shared Each trunk + isolated scalar lanes
```

A plan does not assign concrete Factorio signal names, red/green colors, entity coordinates, or wire
routes.

`mapping/validate.py` independently rechecks semantic coverage, candidate timing equations,
availability/delivery classification, exact lifetimes, shared-resource contracts, and costs. For buses
it verifies lane/lifetime agreement, `BUS_TRANSPORT` ownership, middle span, and private+bus transport
cost.

## Zero-delay wire sum

The first non-ordinary finite candidate uses Factorio network summation:

```text
producer A output: S = a --\
                           +-- shared net carries S = a + b
producer B output: S = b --/
```

No arithmetic combinator represents the semantic `+`, so the candidate adds zero combinator latency
and zero entity cost.

The first candidate remains conservative:

- both summands are operation results;
- each result has exactly one semantic use;
- neither contributor is another semantic addition;
- both contributor outputs occur on the wire-sum phase.

A later n-way aggregation resource can relax these restrictions explicitly.

## Current mapped physical lowering

`lower_stateless_mapping_plan` currently lowers the validated scalar subset:

```text
scalar external Level inputs
scalar constants
ordinary BinaryOp
ordinary Compare
private exact delay chains
zero-delay WIRE_SUM
isolated shared scalar delay buses
```

Mapped bus lowering keeps late physical choices unresolved:

- ingress/egress copies receive fresh abstract lanes;
- trunk stages use `Each + 0 -> Each`;
- coexisting trunk lanes receive explicit `SignalConflict`s;
- concrete signals and red/green colors remain synthesis decisions;
- the entire selected continuous middle span is materialized, even for temporally disjoint lane
  intervals, so emitted combinator count matches the solver objective exactly.

The periodic extractors may contain vector operations and state records, but mapped physical lowering
has not yet been generalized to them. The existing production Level/Event/Snake routes remain
unchanged.

## Snake diagnostics

`benchmarks/snake/analyze_mapping.py` has two intentionally different modes.

### Solved post-update output cone

The accepted Snake benchmark currently has a 60-tick logical period. It can be used strictly as a
throughput comparison input:

```bash
uv run --with 'ortools>=9.14,<10' \
  python -m benchmarks.snake.analyze_mapping \
  --period 60 \
  --compare-private
```

The script does not call `analyze_normalized_state_timing()` and does not copy old computation or
register phases. In this boundary diagnostic, offset-one reads are externalized on `[60, 120)`, all
outputs are demanded at tick 119, and the joint mapper chooses the combinational phases, candidate
kinds, exact transport, and optional buses inside that output cone.

### Phase-neutral full recurrence extraction

To traverse the real Snake recurrence without pretending state-cell timing is already known:

```bash
uv run python -m benchmarks.snake.analyze_mapping \
  --period 60 \
  --extract-full-state
```

This mode reports fixed source count, unresolved state-read occurrences, semantic operation count,
state-transition kinds/occurrences, and sinks, then stops. It never invokes the CP-SAT solver. Its
purpose is to prove the complete workload can be represented at the neutral state boundary before
state-cell candidates are designed.

Neither diagnostic is an accepted replacement blueprint. The established in-game-validated Snake
path remains the correctness oracle.

## Shared resource families

The delay bus demonstrates why not every implementation should be enumerated as a finite candidate.
Its benefit depends on selecting an arbitrary subset of exact lifetimes; enumerating all subsets would
be exponential. The solver therefore represents it with assignment, activation, capacity, and min/max
span variables.

The current bus model is intentionally narrow:

```text
scalar exact lifetimes only
one exact lifetime per semantic producer
continuous shared trunk
conservative persistent-lane capacity
one interface per semantic bus use
no explicit incompatibility-pair API yet
```

Future parameterized resources may include n-way wire aggregation, shared decoder/control networks,
clock distribution, lookup/ROM structures, and time-multiplexed functional units.

## Current progression

1. **done:** ordinary candidate timing plus joint ASAP/interior/ALAP placement;
2. **done:** deterministic plan -> Abstract Physical lowering for the narrow scalar subset;
3. **done:** zero-delay wire sum as the first implementation that changes latency;
4. **done:** shared delay bus as the first parameterized resource family in the same solve;
5. **done:** parity checks against the fixed-placement bus optimizer on controlled unique-tap cases;
6. **done:** periodic output-cone boundary windows and Snake output-cone diagnostic;
7. **current:** phase-neutral full recurrence records (`MappingStateRead`, `MappingStateTransition`) and
   full Snake recurrence extraction;
8. **next:** define periodic state-cell implementation candidates whose read/write port timing is
   conditional on candidate selection, then admit stateful problems into the joint solve;
9. add candidate-dependent output availability propagation, local fusion, and rematerialization;
10. add reusable/time-multiplexed functional units with allocation, binding, latency, and initiation
    interval;
11. extend neutral recurrence constraints to Event/multi-clock domains;
12. add layout-cost feedback only if abstract mapping choices need it.

## Future spatial/temporal sharing

Longer-term, an implementation template may be instantiated fewer times than the number of semantic
operations and reused at different phases. The mapper then owns the familiar high-level synthesis
choices:

```text
allocation:  how many physical instances exist?
binding:     which semantic operation uses which instance?
scheduling:  at which phase/slot?
delivery:    how do operands/results reach their slots?
```

A reusable pipeline must distinguish latency from initiation interval. The eventual user-facing form
is likely close to:

```text
minimize abstract physical area subject to logical period <= P
```

with latency/throughput/area Pareto exploration layered on later. Concrete signal allocation and
layout remain downstream even when temporal sharing becomes aggressive.
