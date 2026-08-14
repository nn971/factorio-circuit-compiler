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

Python execution is elaboration. Symbolic values are logical streams.

Current runtime-open vector operations include arithmetic, `.positive()`, `.any()`, `.gate(...)`, and
selector `.max()`.

## Logical time vocabulary

Inputs and registers use the same observation operation:

```python
x0 = input.sample()
s0 = register.sample()

c.step()

x1 = input.sample()
s1 = register.sample()
```

`step(n)` advances logical time. It is not a Factorio-tick delay. `Circuit.tick()` is reserved for
future explicit physical scheduling and currently raises. `register.value` is compatibility-only.

## Clock-domain timing milestone

Logical and physical time are now separate.

For a state clock domain with physical period `P`, register/value phase `phi`, and logical index `k`:

```text
physical_time(value[k]) = phi + k*P
```

Stateless combinators preserve `k` and add physical latency. Feed-forward latency does not enlarge
`P`; feedback recurrence constraints do.

Ordinary state dependencies force all involved registers into one logical clock domain, even for
one-way dependencies. Independent state components may infer different periods. External physical
inputs do not themselves own a state domain.

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

now infers `P=3` rather than being rejected.

## Physical realization of multicycle state

For each `P>1` domain, vector lowering synthesizes a modulo-`P` clock. Register gates open only on
the scheduled residue:

- `FreezeReg` holds on intermediate physical ticks;
- `AccumulatorReg` suppresses adds and ignores clear between logical boundaries while retaining
  memory.

Thus only one physical input sample per logical window can affect that domain's next state.

Independent state domains with different periods are supported when they use current-step physical
inputs. Nonzero-step external samples across heterogeneous domains are currently rejected until
context-sensitive input realization / explicit resampling is implemented.

## State primitives

`AccumulatorReg` and `FreezeReg` remain the foundational whole-vector state primitives. Higher
structures should first be built from them rather than added as compiler primitives.

A depth-4 FIFO example exists in `examples/vector_fifo.py`. It uses four `FreezeReg`s plus one
`AccumulatorReg` length counter. It now protects full/empty internally, including simultaneous
full-pop+push, and intentionally exercises the new multicycle recurrence. Current timing analysis
expects the FIFO domain to infer `P=5`.

The autonomous-market direction remains:

```python
missing = (required - stock).positive()
request = missing.max()
```

then store/queue selected requests using general state primitives before connecting one reader and
one worker assembler.

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

Abstract physical IR owns exact target combinators, abstract nets/signals, and compatibility
metadata. Physical synthesis owns concrete signal IDs, red/green allocation, placement, wire reach,
and final layout. Blueprint generation only serializes.

## Current validation status

The branch contains focused regressions for:

- `.step()` / `.sample()` frontend semantics and reserved `.tick()`;
- P=1 state timing compatibility;
- state-derived vector predicates;
- `P=3` self-feedback;
- independent heterogeneous domains and domain unification;
- self-validating FIFO composition;
- periodic clock combinator structure.

The assistant environment cannot run the repository locally because GitHub DNS access is unavailable,
and this branch currently has no GitHub CI statuses. Do not claim the suite is green until local checks
are run:

```fish
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

Also validate representative multicycle blueprints in Factorio, especially `vector_fifo.py`.

See `docs/semantics.md` and `docs/state-design.md` for the full timing model.
