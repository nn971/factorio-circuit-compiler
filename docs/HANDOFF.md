# Fresh-Chat Handoff

## Goal

Compile a symbolic Python circuit EDSL to optimized Factorio 2.x combinator blueprints while keeping
logical stream semantics independent from physical combinator timing.

## Canonical frontend

```python
c = Circuit("example")
x = c.input("x")
y = (x + 1) * 2
c.output("y", y)
```

Python execution is elaboration. Symbolic values are logical streams. Runtime-open vector operations
include arithmetic, `.positive()`, `.any()`, `.gate(...)`, and selector `.max()`, including on
register-derived vectors.

## Logical time vocabulary

Inputs and registers use the same observation operation:

```python
x0 = input.sample()
s0 = register.sample()

c.step()

x1 = input.sample()
s1 = register.sample()
```

`step(n)` advances logical time. It is separate from Factorio game ticks. `Circuit.tick()` is reserved
for future explicit physical scheduling and currently raises. `register.value` remains a compatibility
alias; new code uses `register.sample()`.

## Clock-domain timing

Logical and physical time are separate.

For a state clock domain with physical period `P`, register/value phase `phi`, and logical index `k`:

```text
physical_time(value[k]) = phi + k*P
```

Stateless combinators preserve `k` and add physical latency. Feed-forward latency does not enlarge
`P`; feedback recurrence constraints do.

Ordinary state dependencies force the involved registers into one logical clock domain. Independent
state components may infer different periods. External physical inputs do not themselves own a state
domain.

A state dependency with source logical offset `r`, target commit offset `c`, physical latency `L`, and
shared period `P` gives:

```text
phi_target >= phi_source + (r - c - 1) * P + L + 1
```

The analyzer tests integer periods from 1 upward and takes the first feasible one. A positive-latency
same-step combinational cycle remains impossible for every `P`.

Canonical regression:

```python
old = memory.sample()
memory.set(data, when=old.any())
```

infers `P=3` in the current realization rather than being rejected.

## Physical realization of multicycle state

For each `P>1` domain, vector lowering synthesizes a modulo-`P` clock. Register gates open only on the
scheduled residue:

- `FreezeReg` holds on intermediate physical ticks;
- `AccumulatorReg` suppresses adds and ignores clear between logical boundaries while retaining
  memory.

Independent state domains with different periods are supported when they use current-step physical
inputs. Nonzero-step external samples across heterogeneous domains remain deferred until
context-sensitive input realization / explicit resampling is implemented.

## State structures

`AccumulatorReg` and `FreezeReg` are the foundational whole-vector state primitives. Higher structures
should first be built from them rather than added as compiler primitives.

`examples/vector_fifo.py` and `examples/vector_stack.py` are composition regressions. The stack is also
used by the autonomous-market controller.

## Autonomous market status

`examples/autonomous_market_controller.py` has been compiled to a routed blueprint and tested in game
with one recipe-reader assembler and one worker assembler. Recursive prerequisite discovery and
production work. The reader protocol includes one explicit logical settling interval before consuming
its ingredient vector.

The market experiment is intentionally paused at this point. Remaining market-level problems include
stale external stock after a craft, in-flight logistics, raw/uncraftable resources, dependency cycles,
stack overflow, multi-worker scheduling under transport delay, and recipe metadata/ROM design. See
`docs/autonomous-market.md`.

## Physical pipeline

```text
symbolic/logical circuit
    ↓
semantic IR + state timing / clock domains
    ↓
AbstractPhysicalCircuit
    ↓
physical synthesis
    ↓
Layout
    ↓
blueprint serialization
```

Abstract physical IR owns exact target combinators, abstract nets/signals, and compatibility metadata.
Physical synthesis owns concrete signal IDs, red/green allocation, compatible net coalescing,
net-aware placement, reserved access/power corridors, deterministic placement retries, reach-safe
routing, and final layout. Blueprint generation only serializes.

The old direct-concrete backend remains in `compiler_legacy.py` only as a parity/debugging oracle for
selected P=1 regressions.

## Current validation targets

The regression suite covers, among other things:

- `.step()` / `.sample()` frontend semantics and reserved `.tick()`;
- P=1 and multicycle state timing;
- state-derived vector predicates;
- heterogeneous independent domains and domain unification;
- FIFO/stack composition;
- periodic clock structure;
- pairwise arithmetic packing and shared-predicate/multi-output-decider fusion;
- sorting-network and Walsh-Hadamard benchmark circuits;
- abstract physical synthesis, placement, reach-safe routing, and blueprint serialization.

Canonical local checks are:

```fish
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

Do not infer validation status from this document; run the checks for the branch being changed.

## Next technical decision

The strongest semantic candidate is triggered logical domains with input snapshots: treat inferred `P`
as a minimum initiation interval rather than requiring a rigid periodic cadence. This addresses short
external pulses and device-working intervals that can otherwise fall between logical observations.
See `docs/timing-open-problems.md`.

In parallel, use the sorting/WHT examples and `benchmarks/README.md` as the measurement baseline for
future target-graph and physical-synthesis optimization.
