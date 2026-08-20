# Factorio Circuit Compiler

Experimental symbolic Python compiler for Factorio 2.x circuit networks.

The compiler separates logical stream semantics from physical combinator timing:

```text
Python elaboration
    -> semantic CircuitModule
    -> causality and timing analysis
    -> physical lowering
    -> AbstractPhysicalCircuit
    -> physical synthesis + Layout
    -> blueprint
```

Read these first:

- [`docs/data-contract.md`](docs/data-contract.md) — what a circuit means.
- [`docs/compiler-pipeline.md`](docs/compiler-pipeline.md) — where each meaning is compiled.
- [`docs/temporal-lowering-milestone.md`](docs/temporal-lowering-milestone.md) — settling/ALAP lowering and the validated Snake result.
- [`AGENTS.md`](AGENTS.md) — compact contributor/agent rules.

## Minimal Level example

```python
from factorio_circuit import Circuit, compile_circuit

c = Circuit("controller")
a = c.input("a")
b = c.input("b")
limit = c.input("limit")

value = (a + b) * 3
c.output("result", (value > limit).select(limit, value))

compiled = compile_circuit(c)
print(compiled.blueprint_string)
```

Python runs only as elaboration. Symbolic operators build logical stream expressions; the compiler chooses physical phases, state periods, signals, wires, and placement.

## Time and state

Canonical flows carry payload shape, temporal modality, structural clock, and logical occurrence offset. `value.step(n)` is logical occurrence reindexing rather than a Factorio-tick delay.

Level inputs and registers use `.sample()` for observation. `Circuit.step()` remains a compatibility cursor for existing Level/state programs; new temporal expressions should prefer flow-local `.step()`.

`AccumulatorReg` and `FreezeReg` are packed whole-vector state cells. Periodic feedback may infer a physical period greater than one game tick. Production Level lowering treats that period as a settling/deadline budget: validity proofs eliminate unnecessary phase padding, and ALAP scheduling moves ordinary transition-cone computation toward its consumers rather than eagerly computing and delaying every result.

## Events and clock crossings

External Events have payload and presence. Their physical ABI is:

```text
source          payload
source__valid   one-tick occurrence pulse
```

The explicit crossing vocabulary is:

- `sample_on(level, event)` — sample a Level on an Event clock;
- `gate_clock(parent, when=...)` — derive a subclock;
- `event_merge(...)` — additive Event union;
- `hold_into(source, target)` — latest strict-prior source value on the target clock;
- `sum_into(source, target)` — additive source history over `(previous_target, current_target]`.

Sparse outputs materialize as `HOLD`, `ZERO`, or `VALID`. [`examples/README.md`](examples/README.md) is the self-driving in-game semantic ladder; [`examples/clocked_flow.py`](examples/clocked_flow.py) is the compact all-in-one API example.

## Representative examples

- `examples/README.md` — self-driving clock-aware in-game ladder from Event presence to a multi-rate ledger;
- `examples/fibonacci.py` — periodic state and inferred timing;
- `examples/clocked_flow.py` — compact Event clocks, crossings, and materialization;
- `examples/vector_fifo.py` / `vector_stack.py` — state composition;
- `examples/sorting_network.py` / `walsh_hadamard.py` — readable parameterized physical-synthesis stress cases;
- `examples/autonomous_market_controller.py` — application-scale controller.

## Benchmarks

Large workloads live under [`benchmarks/`](benchmarks/README.md) rather than the pedagogical examples.
The primary end-to-end benchmark is [`benchmarks/snake/`](benchmarks/snake/README.md): a playable 16x16
Snake with movement input, periodic state, bounded body history, a 256-lane framebuffer, full physical
synthesis/layout, and in-game acceptance. Its append-only accepted milestones are recorded in
`benchmarks/snake/baselines.json`.

The full Snake framebuffer/state semantic acceptance and the full compile/layout are intentionally
opt-in and are not part of routine pytest/CI. Routine Snake tests use the cheap gameplay model plus a
small stateless renderer check.

## Validation

Routine validation:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

Routine pytest automatically excludes tests marked `slow`, `acceptance`, or `benchmark`. The marker
meanings are intentionally orthogonal:

- `slow` — materially slower than the normal feedback loop;
- `acceptance` — scenario-level end-to-end correctness;
- `benchmark` — performance, scale, census, or layout-oriented validation.

A heavyweight test may carry more than one marker. Explicit `-m` selection overrides the routine
default, so stronger suites stay easy to invoke:

```bash
# Slow and scenario-level pytest checks.
uv run pytest -m "slow or acceptance"

# Pytest-hosted benchmark checks, when present.
uv run pytest -m benchmark

# Everything that routine pytest excludes.
uv run pytest -m "slow or acceptance or benchmark"
```

Useful focused routine regressions include:

```bash
uv run pytest tests/integration/test_multi_rate_event_ledger.py
uv run pytest tests/integration/test_snake.py
```

The autonomous-market controller keeps semantic-shape coverage in routine pytest while its full
optimized/unoptimized compiler acceptance is opt-in:

```bash
uv run pytest -m acceptance tests/integration/test_autonomous_market_controller.py
```

Heavyweight Snake validation remains explicit benchmark tooling rather than pytest:

```bash
# Full semantic framebuffer/reset acceptance; intentionally outside pytest/CI.
uv run python -m benchmarks.snake.semantic_acceptance

# Pre-synthesis census.
uv run python -m benchmarks.snake.census --deep-delays

# Full physical synthesis/layout and blueprint generation.
uv run python -m benchmarks.snake.generate --output snake-blueprint.txt
```

If a routine run unexpectedly stalls, use `uv run pytest -vv` to see the active test and
`uv run pytest --durations=20` to identify slow regressions.

`tests/integration/test_multi_rate_event_ledger.py` is the main irregular-clock end-to-end regression.
Sorting/WHT provide smaller structured synthesis benchmarks; Snake is the heavyweight manual
whole-compiler/layout benchmark.
