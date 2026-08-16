# AGENTS.md

## Read first

Before semantic/compiler work, read:

1. `docs/data-contract.md`
2. `docs/compiler-pipeline.md`

Those two files are the source of truth for the supported data contract and compilation boundaries. Historical milestone notes and completed physical probes are intentionally not kept in the repository.

## Architectural rules

- Python is elaboration. Symbolic values describe logical streams; physical Factorio ticks are chosen later.
- A canonical `Flow` is identified by payload shape, Level/Event modality, structural clock, and logical occurrence offset.
- Ordinary operators preserve clock occurrence. `value.step(n)` changes logical occurrence; it never means `n` game ticks and never silently inserts state.
- Cross-clock behavior is explicit: `SampleOn`, `GateClock`, `EventMerge`, `SumInto`, and `HoldInto`.
- Logical causality is analyzed separately from physical latency/throughput.
- Event presence is distinct from payload value. Physical Events use aligned payload and valid channels.
- State is logical whole-vector state. Preserve atomic reaction semantics and elaboration order.
- Sparse flows acquire `HOLD`, `ZERO`, or `VALID` behavior only at output/device boundaries.
- Both Level and Event lanes must converge on `AbstractPhysicalCircuit`; physical synthesis owns concrete signal allocation, red/green wiring, placement, wire reach, and final `Layout`.
- Blueprint serialization consumes a finished `Layout`; it does not repair geometry or semantic timing.

## Change discipline

Prefer removing obsolete compatibility layers over adding another adapter. Keep a type or pass when it enforces a real invariant; avoid milestone-stage abstractions whose only purpose was incremental implementation.

When changing semantics, add or update a small contract test. When changing lowering/synthesis, compare semantic/reference behavior with physical simulation where possible. Keep `tests/integration/test_multi_rate_event_ledger.py`, sorting, and WHT as broad regression/stress cases.

## Validation

Use Python >= 3.12 and `uv`.

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

Representative focused runs:

```bash
uv run pytest tests/frontend/test_clocked_flow_semantics.py
uv run pytest tests/timing/test_causality.py
uv run pytest tests/integration/test_multi_rate_event_ledger.py
uv run pytest tests/integration/test_layout_benchmark_examples.py
```
