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

`AccumulatorReg` and `FreezeReg` are packed whole-vector state cells. Periodic feedback may infer a physical period greater than one game tick.

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
- `examples/sorting_network.py` / `walsh_hadamard.py` — physical-synthesis stress cases;
- `examples/autonomous_market_controller.py` — application-scale controller.

## Validation

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

`tests/integration/test_multi_rate_event_ledger.py` is the main irregular-clock end-to-end regression. Sorting and WHT remain the structured layout/synthesis benchmarks.
