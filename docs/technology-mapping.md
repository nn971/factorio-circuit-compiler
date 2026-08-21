# Temporal technology mapping

This document defines the architectural boundary for target-aware temporal implementation choices.
It is intentionally separate from the canonical compiler pipeline while the new mapper is being
validated against the established Level/Snake lowering path.

## Boundary

The mapper sits after canonical semantic IR and before Abstract Physical IR:

```text
canonical CircuitModule
    -> implementation-neutral mapping problem
    -> joint temporal technology mapping
    -> RealizationPlan
    -> deterministic mapped lowering
    -> AbstractPhysicalCircuit
    -> signal/wire/layout synthesis
```

The semantic IR answers **what the circuit means**. The mapper answers **which Factorio mechanisms,
physical phases, and shared resources implement that meaning**. Abstract Physical IR records the
chosen combinator/connectivity topology while still leaving concrete signal identities, red/green
wire assignment, placement, and routing unresolved.

### Hard upstream rule

No implementation-dependent latency may be promoted into the implementation-neutral problem.
Upstream constraints may express logical occurrence order, structural clocks, state boundaries,
external availability contracts, and other facts true for every legal implementation. They may not
say that a semantic `BinaryOp`, `Select`, or other recipe necessarily takes the latency of the
current ordinary lowerer.

Candidate timing is conditional on the selected implementation. A one-stage arithmetic candidate
may require operands at `p-1`; a zero-delay wire aggregation candidate may require its contributors
at `p`; a future fused candidate may eliminate an intermediate physical value entirely.

## Mapping problem

`mapping/problem.py` contains semantic recipes and demands:

```text
MappingSource
    semantic leaf + source availability contract

MappingOperation
    semantic recipe + operand value ids

MappingSink
    fixed external demand for one semantic value

MappingUse
    producer/consumer relation before delivery is chosen
```

`MappingProblem` contains no Factorio combinator latency. `MappingSource.semantic` and
`MappingOperation.semantic` retain provenance back to canonical IR so deterministic physical
lowering never has to reconstruct semantic identity from labels.

The first extractor, `build_stateless_level_mapping_problem`, is deliberately narrow. It handles one
stateless Level occurrence with caller-supplied output phases. It does not import periodic state
windows from the established state-timing pass, because those windows already reflect one physical
implementation family.

## Finite implementation candidates

`mapping/templates.py` owns finite local alternatives. A candidate currently records:

```text
semantic operation covered
implementation kind
input phase offsets relative to its output
abstract implementation entity cost
output availability mode
```

Exactly one finite candidate is selected for each first-milestone semantic operation. This
one-operation/one-realization restriction is temporary: future fusion and rematerialization will
allow one candidate to cover several semantic recipes or several physical realizations of one
semantic recipe.

The ordinary candidates deliberately own `FACTORIO_LATENCY`. This is the key boundary test: target
latency first appears when an implementation alternative is introduced, not when semantic causality
is extracted.

## Joint solver

`mapping/solver.py` chooses finite candidates, physical phases, exact lifetimes, and optional shared
delay-bus membership in one CP-SAT model. With buses disabled, the objective is:

```text
selected implementation entity cost
+ prefix-shared private exact-lifetime length
```

Sources may be `STABLE`, `OBSERVABLE`, or `EXACT`. A free source use remains a delivery decision in
the same solve; only the residual part after the last free phase becomes exact transport.

There is deliberately no global ASAP or ALAP mode. ASAP, ALAP, and interior placements are all
possible solutions of the same model.

### Joint delay-bus resource

`solve_mapping_problem(..., max_delay_buses=N)` enables the first parameterized shared-resource
family. Unlike the established `analysis/transport_optimize.py` path, this bus is **not** optimized
after a fixed placement. Its lane starts, ends, membership, and shared middle span are expressions of
the same phase variables used by candidate selection.

For a selected scalar exact lifetime `s -> t`, the first model uses the already validated isolated
Factorio topology:

```text
semantic producer
    -> signal-specific +0 ingress          one tick
    -> bus-private Each + 0 -> Each trunk  shared middle
    -> signal-specific +0 egress           one per transported semantic use
```

A lane can join a bus only when its exact lifetime has length at least three ticks. A bus must have at
least two lanes. The bus middle is continuous from

```text
min(lane.start + 1)
```

to

```text
max(lane.end - 1)
```

and its objective cost is

```text
continuous middle stages
+ one ingress per lane
+ one isolated interface per transported semantic use
```

The first joint model deliberately charges one egress/interface per semantic use even when two uses
happen at the same phase. This keeps CP-SAT cost and emitted hardware exactly one-to-one. Equal-phase
egress coalescing is a future explicit sharing optimization rather than an accidental discrepancy
between plan and lowering.

`max_delay_buses=0` remains the default, so introducing the resource family does not change existing
mapping results unless it is explicitly enabled. `delay_bus_capacity` bounds the number of scalar
lanes on each bus.

## Realization plan

`mapping/plan.py` is the target-aware boundary immediately before mapped physical lowering. It
records:

```text
SelectedRealization
    candidate + output phase for one selected implementation

PlannedDelivery
    reuse / observe-at / private exact transport / bus exact transport

ExactLifetime
    one semantic exact token lifetime before its physical transport is chosen

WireSumResource
    one selected intentional same-carrier aggregation network

DelayBusResource
    one continuous shared Each trunk plus its isolated scalar lanes
```

A plan does not assign concrete Factorio signal names, red/green colors, entity coordinates, or wire
routes. Those remain synthesis/layout decisions.

`mapping/validate.py` independently rechecks semantic coverage, candidate timing equations,
availability/delivery classification, exact lifetimes, explicit shared-resource contracts, and plan
costs. For buses it verifies that lane spans reproduce exact lifetimes, every `BUS_TRANSPORT` use
belongs to exactly one lane, the selected middle span agrees with its lanes, and reported transport
cost equals private lifetime cost plus bus middle/interface cost.

Physical lowering must consume a validated plan rather than silently repairing it.

## First non-ordinary implementation: zero-delay wire sum

Factorio circuit networks add contributions carrying the same signal on one electrical network. The
first `WIRE_SUM` candidate uses that target behavior for a semantic scalar addition:

```text
producer A output: S = a --\
                           +-- shared net carries S = a + b
producer B output: S = b --/
```

No arithmetic combinator represents the semantic `+`, so it adds no combinator phase delay.

The initial candidate is intentionally conservative:

- both summands must be physical operation results;
- each summand result must have exactly one semantic use;
- both producer realizations must output on the wire-sum phase;
- the mapped lowerer gives both output connectors one shared abstract signal and one shared abstract
  net.

These restrictions avoid pretending that connector-channel/red-green resource allocation is already
modeled. A later `WireAggregationResource` can relax them by explicitly accounting for contribution
ports and connector capacity.

## Current mapped physical lowering scope

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

- ingress and egress copies receive fresh abstract signal lanes;
- trunk stages use `Each + 0 -> Each`;
- lanes coexisting on a trunk net receive explicit `SignalConflict` constraints;
- concrete signal identities and red/green wire colors remain physical-synthesis decisions;
- every selected continuous middle stage is materialized even if individual lane intervals are
  temporally disjoint, so the physical combinator count matches the solver's charged span.

This path is still not wired into `compile_circuit()`. The existing Level/Event lowering routes and
the in-game-validated Snake transport path remain unchanged while the new mapper is validated.

## Shared resource families

The delay bus demonstrates why not every implementation should be enumerated as a finite candidate.
Its value comes from assigning an arbitrary subset of exact lifetimes to a shared trunk; enumerating
all subsets would be exponential. The mapper therefore represents it with assignment, activation,
capacity, and min/max span variables rather than one Boolean candidate per subset.

The present bus implementation is still intentionally narrow:

```text
scalar exact lifetimes only
one exact lifetime per semantic producer
continuous shared trunk
conservative persistent-lane capacity
one interface per semantic bus use
no explicit incompatibility-pair API yet
```

Future shared mechanisms may use the same parameterized-resource pattern, for example:

```text
n-way wire aggregation networks
shared decoder/control structures
clock distribution resources
lookup/ROM structures
time-multiplexed functional units
```

The common contract is semantic coverage, timing requirements, produced availability, physical
resource use, and objective cost. The solver representation does not have to be identical for every
family.

## Planned migration order

The current progression is:

1. **done:** ordinary candidate timing plus joint ASAP/interior/ALAP placement;
2. **done:** deterministic plan -> Abstract Physical lowering for the narrow scalar subset;
3. **done:** zero-delay wire sum as the first candidate that changes implementation latency;
4. **current:** shared delay bus as the first parameterized resource family inside the same solve;
5. compare the joint bus model against the established fixed-placement bus optimizer on controlled
   cases, then extend the neutral mapping problem toward the periodic Level/Snake state cone;
6. add local fusion and rematerialization, allowing eliminated or duplicated semantic intermediates;
7. add reusable/time-multiplexed functional units with instance count, binding, latency, and
   initiation interval;
8. extend the neutral constraints to Event/multi-clock domains;
9. add layout-cost feedback only if abstract mapping choices need it, rather than embedding geometry
   into the master solver initially.

Before step 5 changes production behavior, the mapped bus milestone should demonstrate three things:

```text
solver objective < all-private objective on profitable exact lifetimes
validated RealizationPlan records the same bus topology/cost
AbstractPhysicalCircuit emits exactly that number of transport combinators
```

The established Snake path remains the correctness oracle until the periodic neutral constraints can
express its state boundaries without importing implementation-specific timing windows.

## Future spatial/temporal sharing

Longer-term, an implementation template may be instantiated fewer times than the number of semantic
operations and reused at different physical phases. The mapper then owns the familiar high-level
synthesis decisions:

```text
allocation:  how many physical instances exist?
binding:     which semantic operation uses which instance?
scheduling:  at which phase/slot?
delivery:    how do operands/results reach their slots?
```

A reusable pipeline must distinguish latency from initiation interval. This makes the desired
space/throughput tradeoff explicit: hundreds of parallel copies, one fully serialized copy, or any
intermediate number of instances are all solutions of the same target-mapping problem.

The likely user-facing optimization form is eventually:

```text
minimize abstract physical area subject to logical period <= P
```

with latency/throughput/area Pareto exploration added later. Concrete signal allocation and layout
remain downstream even when temporal resource sharing becomes aggressive.
