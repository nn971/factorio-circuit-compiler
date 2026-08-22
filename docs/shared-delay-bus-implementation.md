# Observation-aware shared transport implementation

Status: implementation candidate on `agent/snake-temporal-hypergraph`; not yet an accepted compiler
path.

This note records the implementation that follows `shared-delay-bus-plan.md` and
`shared-delay-bus-decisions.md`.  The older `TemporalPlanLowerer`, coherent-LIVE optimizer, and
isolated temporal-plan experiment remain regression/evidence material rather than the architecture of
this path.

## Pipeline

The candidate keeps production ALAP placement fixed and then runs:

```text
placed temporal hypergraph
    -> temporal availability/alignment
       REUSE
       OBSERVE_AT
       TRANSPORT_TO
    -> residual exact transport optimization
       private scalar/vector chain
       isolated shared scalar bus
    -> Abstract Physical IR
    -> ordinary late signal coloring / wire synthesis / layout
```

`REUSE` and `OBSERVE_AT` are eliminated before transport optimization.  A residual exact transport
starts at the last phase that is still free under same-token validity or fresh observability, not
necessarily at the producer's nominal output phase.

Example:

```text
live result is observable through phase 4
consumer needs one chosen value at phase 7

observe_at(4)       # no hardware
exact transport 4 -> 7
```

The optimizer therefore prices three exact stages rather than incorrectly transporting from the
result's original phase.

## Physical bus representation

`SharedTransportLane.lane_id` is plan-local identity only.  Physical lowering allocates a fresh
`AbstractSignal` at ingress:

```text
semantic producer lane
    -> signal-specific +0 ingress copy
    -> fresh abstract bus lane
    -> shared Each + 0 -> Each middle stages
    -> signal-specific +0 egress copy
    -> fresh abstract consumer-side lane
```

Ingress and egress are deliberately electrically isolating.  The semantic producer signal is never
placed directly on the shared trunk, and a shared trunk signal is never exposed directly to an
ordinary semantic consumer.

When a new bus lane begins coexisting on an already-created carrier, lowering adds explicit
`SignalConflict` edges against the carrier's existing abstract lanes.  Final DSATUR coloring may
still reuse the same concrete Factorio signal for disconnected abstract lane instances elsewhere.

The current continuous bus has no lane-retirement primitive.  Once a lane enters one bus segment it
is conservatively considered present through that segment's end; signal-name reuse is therefore not
based merely on non-overlapping semantic consumer lifetimes.

## Cost model realized by the lowerer

For a lane captured at `s`:

- tap `s + 1`: private one-tick exact branch;
- bus ingress: one signal-specific combinator producing the bus lane at `s + 1`;
- tap `t >= s + 2`: one signal-specific egress from trunk phase `t - 1`;
- shared middle: only `Each + 0 -> Each` stages between ingress and the last trunk phase.

Thus two independent three-tick scalar transports cost six private delay combinators, while one
isolated two-lane bus costs five:

```text
2 ingress + 1 shared middle + 2 egress = 5
```

This is the cost used by `analysis/transport_optimize.py`.

## Scalar Select boundary

The timing-exact temporal builder models scalar `Select` with the target's asymmetric dependency
latencies: two ticks for the condition path and three ticks for data arms.  Temporal alignment also
keeps the result of scalar `Select` exact until the actual lowerer exports a safe fresh-observability
proof.

The timing-exact arithmetic fallback is:

```text
false + (true - false) * condition
```

Its direct `false` contribution needs an implementation-internal exact alignment from the data input
boundary to the final addition.  That alignment is currently emitted explicitly by physical lowering
but is not represented as a semantic `ExactTransportDemand`, so it is not a shared-bus candidate in
this milestone.  Consequently the transport objective is authoritative for planned semantic
transport only; the Abstract Physical census is authoritative for total physical combinator count.

A later milestone can lower `Select` into an explicit physical subgraph before transport planning if
sharing this internal alignment proves worthwhile.

## Regression coverage

The implementation adds focused tests for:

- an ALAP-live scalar whose exact transport begins only at its last free observation phase;
- two exact three-tick scalar lifetimes sharing an isolated bus;
- fresh abstract signal identities at ingress and egress;
- explicit conflicts between lanes coexisting on a bus carrier;
- conservative scalar-`Select` availability and its physical condition boundary;
- complete periodic-module lowering with an all-private residual transport plan.

The new physical-census classifications distinguish bus ingress, shared middle, and bus egress from
ordinary computation.

## Snake diagnostic

The accepted Snake generator remains unchanged.  The candidate has a separate diagnostic runner:

```bash
uv run --with 'ortools>=9.14,<10' \
  python -m benchmarks.snake.generate_transport --census-only
```

This reports:

- counts of `REUSE`, `OBSERVE_AT`, and `TRANSPORT_TO` uses;
- number of residual exact lifetimes and scalar bus candidates;
- the isolated-bus optimization objective;
- full Abstract Physical census;
- implementation-combinator delta versus the accepted ordinary ALAP lowering.

Dropping `--census-only` continues through signal allocation, safe layout, oracle materialization, and
blueprint generation using the same random-food Snake workload as the existing temporal generator.

## Validation status

The branch does not trigger the repository CI workflow by itself, and the current local tool runtime
cannot resolve `github.com`, so this implementation has not been executed from this development
session.  The candidate should not replace the accepted generator until the focused tests and the
Snake census runner have been run in a normal repository checkout and the generated Snake blueprint
has passed the existing in-game behavior check.
