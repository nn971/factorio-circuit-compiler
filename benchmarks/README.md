# Benchmarks

`benchmarks/` contains workloads whose primary purpose is measuring or stress-testing the compiler,
rather than teaching one small language feature.

## Generic physical-layout corpus

`benchmarks/layout_optimizer_corpus.py` is the opt-in structural corpus for the public routed-layout
optimizer. Its cases are deliberately synthetic so that placement/routing behavior can be studied
without coupling the benchmark to one compiler lowering strategy or application.

The current first tranche covers:

- sparse independent implementation entities;
- many independent long relay chains;
- one high-degree shared net with a shared long trunk;
- one long routed span whose implementation endpoints are fixed anchors.

Run one deterministic seed with:

```bash
uv run python -m benchmarks.layout_optimizer_corpus --proposals 256 --seed 0
```

Run a consecutive seed sweep with:

```bash
uv run python -m benchmarks.layout_optimizer_corpus --proposals 256 --seed 0 --seeds 8
```

Every run validates the supplied layout, optimizes through the same public API, validates the returned
layout, and rejects any result whose lexicographic `(relay count, area, wire length)` objective is
worse than its valid input. Multi-seed runs report best/worst objectives and median physical metrics.
The corpus will grow along the structural dimensions recorded in `docs/roadmap.md`.

Cheap CI tests may validate that corpus fixtures themselves are well formed and preserve exact
zero-budget pass-through behavior. Full optimization sweeps remain opt-in.

## Heavyweight end-to-end benchmark

`benchmarks/snake/` is the canonical large application/layout benchmark. It exercises periodic state,
large vector expressions, physical lowering, physical synthesis, layout, blueprint generation, and
real in-game device integration. Full framebuffer/state semantic simulation and the full framebuffer
compile are intentionally **not** part of routine pytest/CI.

Use the cheapest validation tier that covers the change:

```bash
# Routine gameplay/state and cheap stateless renderer coverage.
uv run pytest tests/integration/test_snake.py

# Opt-in full framebuffer/state semantic acceptance.
uv run python -m benchmarks.snake.semantic_acceptance

# Full lowering census without placement/routing.
uv run python -m benchmarks.snake.census --deep-delays

# Full physical synthesis/layout/blueprint build.
uv run python -m benchmarks.snake.generate --output snake-blueprint.txt
```

Accepted Snake milestones and their physical metrics are recorded in
`benchmarks/snake/baselines.json`. Historical entries are append-only: a new accepted optimization gets
a new named milestone rather than rewriting the previous measurement.

## Parameterized benchmark examples

Several smaller parameterized workloads remain under `examples/` because they are also useful readable
demonstrations:

- bitonic sorting networks from `examples/sorting_network.py`;
- Walsh-Hadamard transforms from `examples/walsh_hadamard.py`;
- stateful vector structures such as FIFO/stack and the autonomous-market controller when timing or
  state realization is under test.

`tests/integration/test_layout_benchmark_examples.py` verifies semantic results, representative-size
compilation, real blueprint serialization, and selected combinator-count regressions for those smaller
cases. Physical synthesis also exposes `placement_metrics(...)` for geometry-oriented comparisons.

## What to record

When comparing compiler strategies, record at least:

- physical combinator/entity count;
- physical net/group count;
- output phase / latency;
- inferred state-domain periods when applicable;
- realized relay and wire counts;
- realized width, height, and bounding-box area;
- layout-specific track/row/column counts when meaningful;
- compiler/synthesis runtime as informational machine-dependent data;
- whether the final blueprint was validated in game when the benchmark has a manual acceptance path.

Keep benchmark assertions focused. Exact counts are useful for deliberate stable regression guards, but
exploratory optimizer measurements should not turn every current heuristic into an architectural
contract. Heavy benchmarks should be opt-in unless their runtime becomes small enough for routine CI.
