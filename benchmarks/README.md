# Benchmarks

`benchmarks/` contains workloads whose primary purpose is measuring or stress-testing the compiler,
rather than teaching one small language feature.

## Heavyweight end-to-end benchmark

`benchmarks/snake/` is the canonical large application/layout benchmark. It exercises periodic state,
large vector expressions, physical lowering, physical synthesis, layout, blueprint generation, and
real in-game device integration. Its full framebuffer compile is intentionally **not** part of routine
pytest/CI.

Run it explicitly with:

```bash
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
