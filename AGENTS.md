# AGENTS.md

## Repository purpose

This repository compiles symbolic Python circuit descriptions into Factorio circuit-network
blueprints.

Canonical pipeline:

1. symbolic frontend / logical streams
2. semantic IR with explicit state transitions
3. abstract physical IR with exact Factorio combinators and abstract nets/signals
4. physical synthesis: concrete signals, wire colors, placement/layout
5. blueprint serialization

Physical synthesis owns resource allocation and layout. Blueprint generation only serializes the
final `Layout`.

## Timing invariants

Logical time and Factorio game ticks are different coordinates.

- `source.sample()` observes an input or register at the current logical step.
- `circuit.step(n)` advances logical time by `n` steps.
- `Circuit.tick()` is reserved for future explicit physical-tick control and currently must not be
  used as a logical-time alias.
- `register.value` is compatibility-only; new code uses `register.sample()`.
- Stateless combinators preserve logical step and add physical latency.
- Each connected state clock domain has an inferred integer physical period `P`.
- A register/value phase `phi` maps logical step `k` to physical tick `phi + k*P`.
- Feed-forward latency does not increase `P`; recurrence constraints do.
- Ordinary state dependencies force the involved registers into the same logical clock domain.
- Independent state components may have different periods.
- Genuine state communication across different periods requires explicit future rate-crossing
  semantics; do not silently invent same-index behavior.
- External inputs are physical sources and may conceptually be sampled by multiple domains.
- A zero-logical-distance positive-latency cycle remains illegal for every period.

For a state dependency with source logical offset `r`, target commit offset `c`, physical latency
`L`, shared period `P`, and register phases `phi`, the analyzer uses the difference constraint

```text
phi_target >= phi_source + (r - c - 1) * P + L + 1
```

and chooses the smallest feasible integer `P` per domain.

For `P>1`, physical lowering must gate state writes so intermediate game ticks hold state. Period
inference without physical commit gating is incorrect.

## Frontend / state

`Circuit`, `Expr`, `SignalsExpr`, `AccumulatorReg`, and `FreezeReg` are the public symbolic frontend.
Whole-vector runtime-open expressions currently include arithmetic, positive filtering, `any()`,
`gate()`, and selector `max()`.

`AccumulatorReg` and `FreezeReg` are foundational state primitives. Prefer constructing higher
structures such as queues/stacks from general primitives before proposing compiler-specific state
objects.

Register reads and updates retain strict elaboration ordering. A read cannot split one compound
state transition.

## Abstract physical IR

Abstract physical IR owns:

- exact target combinator kinds and operations;
- abstract nets and signal identities/domains;
- compatibility/conflict metadata;
- logical physical phases needed for realization.

Physical synthesis owns:

- concrete Factorio signal IDs;
- red/green allocation;
- placement and wire reach;
- final `Layout`.

Runtime-open vector nets and fixed target signals must remain explicit. `Each` is the first-class
whole-vector mechanism. Both red and green networks are usable resources.

Factorio combinators add one physical game tick. Wire reach must be respected; invalid long wires
must never be silently emitted.

## Validation

Validate semantic changes at three levels when applicable:

1. logical/semantic tests;
2. physical tick-level simulation or structural timing tests;
3. generated blueprints tested in Factorio for representative circuits.

Canonical compiler entrypoint is `compile_circuit`; `compile_abstract_circuit` is a compatibility
alias.

Local checks expected before declaring a change green:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

Do not claim these checks passed unless they were actually run.
