# AGENTS.md

## Read first

Before semantic/compiler work, read:

1. `docs/data-contract.md`
2. `docs/compiler-pipeline.md`
3. `docs/factorio-2-circuit-mechanics.md`

These are the source of truth for logical semantics, compilation boundaries, and target-game mechanics that materially constrain the compiler. `docs/README.md` indexes the remaining current subsystem and experimental documentation.

Historical milestone notes, branch handoffs, benchmark experiment diaries, and completed physical probes belong in Git/PR history rather than permanent documentation.

## Architectural rules

- Python is elaboration. Symbolic values describe logical streams; physical Factorio ticks are chosen later.
- A canonical `Flow` is identified by payload shape, Level/Event modality, structural clock, and logical occurrence offset.
- Ordinary operators preserve clock occurrence. `value.step(n)` changes logical occurrence; it never means `n` game ticks and never silently inserts state.
- Cross-clock behavior is explicit: `SampleOn`, `GateClock`, `EventMerge`, `SumInto`, and `HoldInto`.
- Logical causality is analyzed separately from physical latency/throughput.
- Event presence is distinct from payload value. Physical Events use aligned payload and valid channels.
- State is logical whole-vector state. Preserve atomic reaction semantics and elaboration order.
- Sparse flows acquire `HOLD`, `ZERO`, or `VALID` behavior only at output/device boundaries.
- Both Level and Event lanes converge on `AbstractPhysicalCircuit`; physical synthesis owns concrete signal allocation, red/green wiring, placement, wire reach, and final `Layout`.
- Blueprint serialization consumes a finished `Layout`; it does not repair geometry or semantic timing.
- Factorio 2.x constant combinators are 1x1 whole-vector sources. One entity can emit a configured value for every signal lane in the vector. Never estimate entity count by dividing configured lanes by 20 or 50; consult `docs/factorio-2-circuit-mechanics.md` before ROM/storage sizing.

## Physical-layout invariants

See `docs/physical-layout.md` for the current layout contract. In particular:

- Feasibility comes first: optimization starts from an explicitly reach-safe routed topology and must return a validated topology.
- Implementation combinators and relay constant combinators share the same legal/corridor-aware workspace.
- Optimizer geometry is authoritative; serialized relay coordinates and wires must be validated exactly.
- Explicit user anchors remain fixed. Automatic public I/O anchors may be recomputed when the occupied envelope changes.
- Failed sequential relay routing can be a cross-net congestion artifact; one greedy net order does not prove global infeasibility.

## Change discipline

Prefer removing obsolete compatibility layers over adding another adapter. Keep a type or pass when it enforces a real invariant; avoid milestone-stage abstractions whose only purpose was incremental implementation.

When changing semantics, add or update a small contract test. When changing lowering/synthesis, compare semantic/reference behavior with physical simulation where possible. Keep `tests/integration/test_multi_rate_event_ledger.py`, sorting, and WHT as broad regression/stress cases.

Routine pytest must stay routine. Do not put the full 16x16 Snake framebuffer/state simulation or full Snake compile/layout into `tests/`; those are opt-in benchmark acceptance tasks under `benchmarks/snake/`. Prefer a small fixture, stateless renderer check, or `render_framebuffer=False` gameplay test for ordinary regression coverage.

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
uv run pytest tests/integration/test_snake.py
```

Opt-in heavyweight Snake validation:

```bash
uv run python -m benchmarks.snake.semantic_acceptance
uv run python -m benchmarks.snake.census --deep-delays
uv run python -m benchmarks.snake.generate --output snake-blueprint.txt
```

If routine pytest unexpectedly becomes slow, diagnose before adding exclusions:

```bash
uv run pytest -vv
uv run pytest --durations=20
```
