# Factorio Circuit Compiler

Experimental compiler from a symbolic Python EDSL to optimized Factorio 2.x combinator circuits and
blueprints.

```text
ordinary Python elaboration
        ↓
Circuit / Input / Expr objects
        ↓
logical stream DAG
  - scalar stateless expressions
  - explicit fresh source samples
  - built-in whole-vector state components
        ↓
optimization + state realization
        ↓
abstract physical Factorio IR
        ↓
physical synthesis
        ↓
Layout
        ↓
blueprint JSON/string serialization
```

Start with:

- [`docs/HANDOFF.md`](docs/HANDOFF.md) — shortest fresh-chat context
- [`docs/conventions.md`](docs/conventions.md)
- [`docs/semantics.md`](docs/semantics.md)
- [`docs/state-design.md`](docs/state-design.md)
- [`docs/architecture.md`](docs/architecture.md)
- [`docs/optimizer-notes.md`](docs/optimizer-notes.md)

## Symbolic frontend

Python executes normally and constructs a circuit graph through symbolic objects:

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

`a`, `b`, `total`, and `result` represent logical signal streams. Arithmetic and comparison operators
construct semantic-IR nodes. Physical combinator phases remain opaque until compilation.

Python `if` and loops remain ordinary elaboration-time Python. Runtime circuit selection uses
`condition.select(when_true, when_false)`.

## Fresh source sampling

Raw input objects are temporal sources and provide `.sample()`:

```python
c = Circuit("fresh")
x = c.input("x")

x0 = x  # stream X[t]
c.tick(3)  # freshness cursor becomes +3
x3 = x.sample()  # stream X[t+3]

c.output("sum", x0 + x3)
```

A derived `Expr` has opaque execution timing and therefore has no `.sample()` method.

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
c.tick(1)
c.output("memory", memory.value)
```

An accumulator may have multiple commutative additive sources, each with an independent enable:

```python
memory.add(source_a, when=enable_a)
memory.add(source_b, when=enable_b)
```

All adds and the optional clear belong to one compound transition per invocation.

### `FreezeReg`

```python
c = Circuit("freeze")
data = c.signals("data")
set_signal = c.input("set_signal")
memory = c.freeze("memory")

memory.set(data, when=set_signal)
c.tick(1)
c.output("memory", memory.value)
```

Agreed polarity:

```text
set != 0   pass/track
set == 0   freeze/hold last passed vector
```

State accesses are strictly ordered by Python elaboration order in the v1 source semantics. A state
read names an exact logical boundary. Therefore a post-update read normally advances the freshness
cursor at least one tick, while a previous value can be captured before issuing the update. The IR
already assigns individual order identities to reads and writes so a future explicit update-event API
can relax this without replacing the state representation.

## Implemented prototype

- scalar arithmetic/bitwise expression DAGs;
- comparisons and symbolic `select`;
- multiple named outputs;
- fresh scalar and whole-vector external sampling;
- simplification, CSE, DCE, conservative `Each` ALU packing;
- phase-aware lowering and tick-accurate physical simulation;
- an abstract state-timing plan with compiler-chosen elastic commit offsets;
- a reference semantic simulator for the current vector registers;
- mutually coupled state-to-state whole-vector feeds;
- multiple conditional commutative `AccumulatorReg.add(...)` sources;
- constant whole-vector streams and direct observation of concrete signal lanes with `.signal(...)`;
- a switchable Fibonacci reference circuit exercising coupled state, post-transition reads, and hold/resume behavior;
- strict feasibility errors for same-boundary post-update reads and reads splitting compound updates;
- abstract physical lowering plus late net-color and concrete-signal synthesis;
- reach-safe blueprint routing with intentionally simple deterministic row placement;
- working in-game `AccumulatorReg` and `FreezeReg` vector-state prototypes.

The physical backend is intentionally frozen at this simple placement policy while development returns
to semantic features. See `docs/architecture.md` and `docs/state-design.md`.

## Development

With `uv` and fish:

```fish
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

Examples:

```fish
uv run python examples/three_adds.py
uv run python examples/branching.py
uv run python examples/fresh_sample.py
uv run python examples/n_tick_pulse.py
uv run python examples/accumulator_reg.py
uv run python examples/freeze_reg.py
uv run python examples/fibonacci.py
```
