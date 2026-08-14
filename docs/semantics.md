# Circuit semantics

## Two time coordinates

The source language describes **logical streams**. Factorio realizes them with a physical pipeline.
Those are deliberately different coordinates.

For a logical clock domain `D`, logical step `k` is activated every `P_D` game ticks. A value `v`
in that domain has a physical phase `phi_v`, so its realization repeats at

```text
physical_time(v[k]) = k * P_D + phi_v
```

(up to an irrelevant common origin for the domain).

`P_D` is the domain's physical **period / initiation interval**. `phi_v` is ordinary pipeline
latency inside that schedule. A long feed-forward pipeline may have a large `phi` while still using
`P=1`.

## Source vocabulary

Use one operation for observing any source:

```python
x = input.sample()
s = register.sample()
```

Both mean: observe this source at the circuit's current logical step.

Advance logical time with:

```python
circuit.step()
circuit.step(n)
circuit.step_until(n)
```

`step(n)` advances the logical sequence index by `n`; it does **not** promise `n` Factorio ticks.

`Circuit.tick()` is intentionally reserved for future explicit physical-tick scheduling control and
currently raises an error. `register.value` remains only as a compatibility alias for
`register.sample()` while old callers migrate.

## Stateless expressions

Arithmetic, comparisons, selects, and runtime-open vector operations preserve logical step:

```text
x[k] -> combinational logic -> y[k]
```

Every physical combinator adds its normal game-tick latency. The compiler inserts physical delays
when two logically compatible values arrive at different phases.

A feed-forward path does not by itself restrict `P`; it is a pipeline and may accept a new logical
sample every physical tick.

## Inputs

Raw scalar/vector inputs are physical sources rather than stateful logical clock domains. A domain
samples them at that domain's activation cadence.

```python
x0 = x.sample()  # x[k]
circuit.step()
x1 = x.sample()  # x[k+1]
```

If the consuming domain has `P=4`, these observations correspond to physical source ticks separated
by four game ticks. Intermediate physical source values are not separate samples for that domain.
Pulse-like sources therefore need an appropriate fast domain or explicit event capture/buffering.

Purely external stateless circuits use the default `P=1` schedule.

## State

A register observation is a logical state sample:

```python
old = state.sample()  # S[k]
state.set(next_value, when=enable)
circuit.step()
new = state.sample()  # S[k+1]
```

The update methods describe the transition; `step()` only moves the logical observation cursor.
It does not imperatively execute the update.

`AccumulatorReg` and `FreezeReg` retain their existing logical transition semantics. A state
transition may take several physical ticks. The compiler infers the smallest legal period and gates
the physical memory so it commits only at logical boundaries; intermediate game ticks hold state.

## Clock domains

Ordinary expressions have same-index semantics. Therefore state registers connected by an ordinary
state dependency must share one logical clock domain.

Examples that unify domains:

```text
A[k] -> B[k+1]
A[k] + B[k] -> C[k+1]
A[k] + B[k] -> output[k]
```

Domain inference starts with one candidate domain per state register and unions registers that occur
in the same ordinary dependency relation. The transitive closure gives maximal ordinary same-index
components.

Independent state components may keep different periods. For example one recurrence can use `P=1`
while an unrelated recurrence uses `P=3`.

External inputs do not union domains: multiple domains may sample the same physical source at their
own cadence.

State-to-state communication between genuinely different periods is **not** an ordinary expression.
It requires an explicit future clock-domain-crossing operation (sample/hold, event accumulation,
FIFO, etc.) that defines which source logical value is observed.

Current lowering restriction: a nonzero-step external `InputSample` shared by heterogeneous state
domains is rejected until domain-contextual input realization is implemented. This is a backend
limitation, not a different semantic rule.

## Timing constraints and inferred period

For one domain, let register/value phases be `phi`. A dependency from source register state sampled
at logical offset `r` to a target transition committing after logical offset `c` has physical logic
latency `L`.

The target transition-input phase is

```text
phi_target + (c + 1) * P - 1
```

and the source becomes available at

```text
phi_source + r * P + L
```

so the compiler requires

```text
phi_target >= phi_source + (r - c - 1) * P + L + 1
```

For a cycle, the `phi` terms cancel. Positive logical distance permits a finite `P`; a same-step
physical cycle does not.

For the common recurrence

```text
S[k] -- L ticks of logic --> S[k+1]
```

we get

```text
P >= L + 1
```

where the final `+1` is the state-writing combinator stage.

Example:

```python
old = memory.sample()
memory.set(data, when=old.any())
```

`old.any()` needs one combinator tick and state-control normalization needs another. The write gate
is the final stage, so the minimum period is `P=3` rather than an error.

A genuine combinational cycle has zero logical distance and positive physical latency. No choice of
`P` can satisfy it, so it remains illegal.

## Physical realization of P > 1

Each multicycle domain gets a synthesized modulo-`P` clock signal. State update gates open only on
the residue assigned to that register's phase. On other physical ticks:

- `FreezeReg` forces its memory path to hold;
- `AccumulatorReg` suppresses additions and ignores clear requests while retaining memory.

Thus physical inputs may continue changing every game tick, but only values aligned with a logical
activation can affect that domain's transition.

## Elaboration order and logical steps

Register reads and update calls still carry strict elaboration order. Reads may occur before or after
one compound transition, but a read cannot split the operations belonging to that transition.

A post-update observation must advance at least one logical step:

```python
old = state.sample()
state.set(x, when=enable)
circuit.step()
new = state.sample()
```

The timing analyzer chooses a realizable physical phase and period consistent with these logical
constraints; source code does not name the write's physical game tick.
