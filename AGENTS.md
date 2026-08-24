# AGENTS.md

## Read first

Before semantic/compiler work, read:

1. `docs/data-contract.md`
2. `docs/compiler-pipeline.md`

Those two files are the source of truth for the supported data contract and compilation boundaries. Historical milestone notes and completed physical probes are intentionally not kept in the repository.

For continuation of the active annealed-layout work on PR #34, also read `handoff.md`. It is a branch handoff, not a replacement for the stable architecture documents above.

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
- Constant combinators are 1x1 entities and can emit a whole signal vector. Do not reintroduce stale 20-value or 50-value capacity assumptions.

### Annealed physical-layout invariants

- The joint annealer starts from an explicitly reach-safe topology and must remain in the feasible region. Ordinary proposals may inspect only local incident wires; expensive topology work belongs outside the hot loop.
- Implementation combinators and relay constant combinators share the same corridor-aware legal workspace. Reserved corridors are unavailable to both classes of entity.
- `_JointState.relay_positions` is the geometric source of truth while optimizing. Any `RoutingPlan` returned outside the optimizer must materialize relay coordinates from that state, and final validation must check the exact coordinates that will be serialized.
- If a bootstrap grid expands, automatic public I/O anchors must be recomputed from the expanded bounds before routing. Explicit user anchors always override automatic anchors.
- A failed sequential relay allocation may be a cross-net congestion problem even when both endpoints have many free neighbors. Do not infer global infeasibility from one greedy net order.

## Change discipline

Prefer removing obsolete compatibility layers over adding another adapter. Keep a type or pass when it enforces a real invariant; avoid milestone-stage abstractions whose only purpose was incremental implementation.

When changing semantics, add or update a small contract test. When changing lowering/synthesis, compare semantic/reference behavior with physical simulation where possible. Keep `tests/integration/test_multi_rate_event_ledger.py`, sorting, and WHT as broad regression/stress cases.

Routine pytest must stay routine. Do not put the full 16x16 Snake framebuffer/state simulation or full Snake compile/layout into `tests/`; those are opt-in benchmark acceptance tasks under `benchmarks/snake/`. Prefer a small fixture, stateless renderer check, or `render_framebuffer=False` gameplay test for ordinary regression coverage.

## Validation

Use Python >= 3.12 and `uv`.

Routine validation:

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

Opt-in heavyweight Snake validation, only when the affected work justifies it:

```bash
# Full semantic framebuffer/reset acceptance; intentionally outside pytest/CI.
uv run python -m benchmarks.snake.semantic_acceptance

# Full lowering census without placement/routing.
uv run python -m benchmarks.snake.census --deep-delays

# Full physical synthesis/layout/blueprint generation; multi-minute benchmark.
uv run python -m benchmarks.snake.generate --output snake-blueprint.txt
```

For PR #34 annealed-layout acceptance, use the exact benchmark command recorded in `handoff.md` and test the resulting blueprint in Factorio; green CI alone does not validate the heavyweight physical layout.

If routine pytest unexpectedly becomes slow, diagnose before adding exclusions:

```bash
uv run pytest -vv
uv run pytest --durations=20
```
