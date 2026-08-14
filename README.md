# Factorio Circuit Compiler

Experimental compiler from a symbolic Python EDSL to optimized Factorio 2.x combinator circuits and
blueprints.

The following is the Level/physical compilation pipeline; Event-bearing circuits branch to the
semantic/reference lane after frontend elaboration and semantic `CircuitModule` construction.

```text
ordinary Python elaboration
        ↓
logical stream graph
  - scalar/vector expressions
  - logical source samples
  - explicit state transitions
        ↓
logical clock-domain + state timing analysis
        ↓
abstract physical Factorio IR
        ↓
physical synthesis
        ↓
Layout
        ↓
blueprint JSON/string serialization
```

Event-bearing modules use a separate semantic/reference lane. Frontend elaboration still constructs a
semantic `CircuitModule`; `simulate_events()` runs declared Event schedules, `Circuit.sample_on(...)`
records Level snapshots on Event activations, and `materialize_event_trace(...)` applies reference-only
HOLD/ZERO/VALID policies. Level/physical routes then raise `EventCompilationError` before
`StateTimingPlan` or semantic-to-physical lowering, so no abstract physical IR, synthesis, blueprints,
or Level simulation follows. They do not provide physical Event pulses, storage, bridges, or valid
wiring.

Start with:

- [`docs/HANDOFF.md`](docs/HANDOFF.md)
- [`docs/conventions.md`](docs/conventions.md)
- [`docs/semantics.md`](docs/semantics.md)
- [`docs/state-design.md`](docs/state-design.md)
- [`docs/architecture.md`](docs/architecture.md)

## Symbolic frontend

```python
from factorio_circuit import Circuit, compile_circuit

c = Circuit("controller")
a = c.input("a")
b = c.input("b")
limit = c.input("limit")

total = (a + b) * 3
result = (total > limit).select(limit, total)

c.output("result", result)
compiled = compile_circuit(c)
```

Symbolic values are logical streams. Arithmetic and comparisons construct semantic IR; physical
combinator phases remain compiler concerns.

## Logical sampling and steps

Inputs and state use the same observation vocabulary:

```python
x0 = input.sample()
s0 = state.sample()

c.step(1)

x1 = input.sample()
s1 = state.sample()
```

`step(n)` advances **logical** time. It does not mean `n` Factorio game ticks. `Circuit.tick()` is
reserved for future explicit physical scheduling and currently raises an error.

Derived expressions do not have `.sample()` because they already denote a sampled logical stream.

## Inferred physical period

A stateful logical clock domain has an inferred physical period `P`. Logical state `S[k]` is realized
at physical times separated by `P` game ticks. Feed-forward pipeline latency does not increase `P`;
feedback recurrences do.

For example:

```python
old = memory.sample()
memory.set(data, when=old.any())
```

is legal even though the state decision needs several combinator ticks. The current realization
infers `P=3` and gates the register so intermediate physical ticks hold state.

Ordinary state dependencies share one logical clock domain. Independent state components may infer
different periods; explicit cross-domain state resampling is future work.

See [`docs/semantics.md`](docs/semantics.md) for the timing equations and domain rules.

## Whole-vector state

Whole Factorio signal maps are declared with `c.signals(...)`.

### `AccumulatorReg`

```python
c = Circuit("accumulator")
data = c.signals("data")
clear = c.input("clear")
memory = c.accumulator("memory")

memory.add(data)
memory.clear(when=clear)
c.step()
c.output("memory", memory.sample())
```

An accumulator may have multiple commutative additive sources with independent enables.

### `FreezeReg`

```python
c = Circuit("freeze")
data = c.signals("data")
set_signal = c.input("set_signal")
memory = c.freeze("memory")

memory.set(data, when=set_signal)
c.step()
c.output("memory", memory.sample())
```

Polarity:

```text
set != 0   pass/track at a logical boundary
set == 0   hold the previous vector
```

`register.value` is a deprecated compatibility alias retained for old callers; new code uses
`register.sample()`.

## Runtime-open vectors

Current whole-vector operations include:

```python
missing = (required - stock).positive()
has_missing = missing.any()
request = missing.max()
gated = request.gate(enable)
```

The vector remains runtime-open: signal identities need not be known during frontend elaboration.

## Development

With `uv` and fish:

```fish
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

Representative examples:

```fish
uv run python examples/fresh_sample.py
uv run python examples/accumulator_reg.py
uv run python examples/freeze_reg.py
uv run python examples/fibonacci.py
uv run python examples/vector_deficit.py
uv run python examples/state_vector_predicate.py
uv run python examples/vector_fifo.py
```

Level modules should be validated with tick-level physical simulation and generated blueprints in
Factorio for representative stateful circuits. Event/reference behavior is validated separately with:

```fish
uv run pytest tests/integration/test_events.py
```

Blueprint validation is Level-module-only; this project does not claim Factorio physical Event support.
