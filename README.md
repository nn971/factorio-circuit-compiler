# Factorio Circuit Compiler

Experimental symbolic Python compiler for Factorio 2.x circuit networks.

```text
Python elaboration
    -> semantic CircuitModule
    -> causality / timing analysis
    -> physical lowering
    -> AbstractPhysicalCircuit
    -> physical synthesis + Layout
    -> blueprint
```

`compile_circuit()` is the canonical orchestration entry point. Python executes only as elaboration; symbolic values describe logical streams, while the compiler chooses physical phases, signals, wires, placement, routing, and blueprint serialization.

## Read first

- [`docs/data-contract.md`](docs/data-contract.md) — logical Level/Event/clock/state semantics.
- [`docs/compiler-pipeline.md`](docs/compiler-pipeline.md) — ownership and compilation boundaries.
- [`docs/factorio-2-circuit-mechanics.md`](docs/factorio-2-circuit-mechanics.md) — verified target-game facts that constrain architecture.
- [`docs/README.md`](docs/README.md) — current documentation map.
- [`AGENTS.md`](AGENTS.md) — compact contributor/agent invariants.

In particular, Factorio 2.x constant combinators are whole-vector sources: one entity can emit a configured value for every signal lane in the vector. Do not use legacy 20-value or 50-value capacity assumptions when sizing ROMs or other vector sources.

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

Canonical flows carry payload shape, Level/Event modality, structural clock, and logical occurrence offset. `value.step(n)` reindexes logical occurrences; it is not a Factorio-tick delay. Cross-clock behavior is explicit through `sample_on`, `gate_clock`, `event_merge`, `hold_into`, and `sum_into`.

External Events lower to aligned payload and presence channels. State remains logical whole-vector state; physical state timing is selected later by timing/lowering.

## Examples and benchmarks

[`examples/README.md`](examples/README.md) contains small pedagogical examples. Large workloads live under [`benchmarks/`](benchmarks/README.md).

The primary end-to-end benchmark is [`benchmarks/snake/`](benchmarks/snake/README.md), a playable 16x16 Snake workload with periodic state, external movement input, random-food support, a 256-lane framebuffer, full physical synthesis/layout, and in-game acceptance. Accepted benchmark measurements belong in `benchmarks/snake/baselines.json`; experiment diaries belong in Git/PR history rather than permanent docs.

## Validation

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

Routine pytest excludes tests marked `slow`, `acceptance`, or `benchmark`. Heavyweight Snake checks stay explicit:

```bash
uv run python -m benchmarks.snake.semantic_acceptance
uv run python -m benchmarks.snake.census --deep-delays
uv run python -m benchmarks.snake.generate --output snake-blueprint.txt
```

Useful focused regressions include:

```bash
uv run pytest tests/integration/test_multi_rate_event_ledger.py
uv run pytest tests/integration/test_layout_benchmark_examples.py
uv run pytest tests/integration/test_snake.py
```
