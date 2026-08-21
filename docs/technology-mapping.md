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

`mapping/solver.py` chooses candidate and phase in one CP-SAT model. The first objective is:

```text
selected implementation entity cost
+ prefix-shared private exact-lifetime length
```

Sources may be `STABLE`, `OBSERVABLE`, or `EXACT`. A free source use remains a delivery decision in
the same solve; only the residual part after the last free phase becomes exact transport.

There is deliberately no global ASAP or ALAP mode. ASAP, ALAP, and interior placements are all
possible solutions of the same model.

## Realization plan

`mapping/plan.py` is the target-aware boundary immediately before mapped physical lowering. It
records:

```text
SelectedRealization
    candidate + output phase for one selected implementation

PlannedDelivery
    reuse / observe-at / private exact transport for one semantic use

ExactLifetime
    one prefix-shareable exact token lifetime

WireSumResource
    one selected intentional same-carrier aggregation network
```

A plan does not assign concrete Factorio signal names, red/green colors, entity coordinates, or wire
routes. Those remain synthesis/layout decisions.

`mapping/validate.py` independently rechecks semantic coverage, candidate timing equations,
availability/delivery classification, exact lifetimes, explicit shared-resource contracts, and plan
costs. Physical lowering must consume a validated plan rather than silently repairing it.

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
```

It is not wired into `compile_circuit()`. The existing Level/Event lowering routes remain canonical
and unchanged while this path is validated.

## Shared resource families

Not every useful implementation should be enumerated as a finite candidate. A delay bus is useful
because an arbitrary subset of exact lifetimes can share one trunk; enumerating every subset would be
exponential.

The intended extension is a parameterized resource-family interface whose solver variables describe
membership and shared geometry/capacity. The existing validated shared-delay-bus model is the first
resource family to migrate after the finite candidate/timing boundary is stable.

Other mechanisms may use the same pattern when sharing is combinatorial, for example:

```text
wire aggregation networks
shared decoder/control structures
clock distribution resources
lookup/ROM structures
```

The common contract is semantic coverage, timing requirements, produced availability, physical
resource use, and objective cost. The solver representation does not have to be identical for every
family.

## Planned migration order

The intended order is:

1. validate ordinary candidate timing plus joint ASAP/interior/ALAP placement;
2. validate deterministic plan -> Abstract Physical lowering;
3. validate zero-delay wire sum as the first candidate that changes latency;
4. migrate the established shared-delay bus as a parameterized resource family;
5. add local fusion and rematerialization, allowing eliminated or duplicated semantic intermediates;
6. add reusable/time-multiplexed functional units with instance count, binding, latency, and
   initiation interval;
7. extend the neutral constraints to periodic state and later Event/multi-clock domains;
8. add layout-cost feedback only if abstract mapping choices need it, rather than embedding geometry
   into the master solver initially.

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
