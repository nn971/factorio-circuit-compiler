# Benchmarks

`benchmarks/` contains workloads whose primary purpose is measuring or stress-testing the compiler,
rather than teaching one small language feature.

## Generic physical-layout corpus

`benchmarks/layout_optimizer_corpus.py` and `benchmarks/layout_optimizer_topology_corpus.py` form the
opt-in structural corpus for the public routed-layout optimizer. Their cases are deliberately
synthetic so that placement/routing behavior can be studied without coupling the benchmark to one
compiler lowering strategy or application.

The current corpus covers:

- sparse independent implementation entities;
- many independent long relay chains;
- one high-degree shared net with a shared long trunk;
- one long routed span whose implementation endpoints are fixed anchors;
- a one-tile routing corridor enforced by forbidden regions;
- mixed 1x1 constant-combinator and 2x1 arithmetic-combinator footprints;
- an anchor-heavy perimeter interface with fixed public terminals around movable body logic;
- compact local clusters connected by a sparse inter-cluster cut net;
- crossing red/green mesh connectivity;
- an already-packed near-optimal starting embedding;
- an explicitly opt-in 1,200-object sparse compaction case.

Run the first structural tranche with one deterministic seed:

```bash
uv run python -m benchmarks.layout_optimizer_corpus --proposals 256 --seed 0
```

Run the topology tranche with one deterministic seed:

```bash
uv run python -m benchmarks.layout_optimizer_topology_corpus --proposals 256 --seed 0
```

Run consecutive seed sweeps with `--seeds`, for example:

```bash
uv run python -m benchmarks.layout_optimizer_corpus --proposals 256 --seed 0 --seeds 8
uv run python -m benchmarks.layout_optimizer_topology_corpus --proposals 256 --seed 0 --seeds 8
```

The 1,200-object case is excluded from the default topology tranche and must be requested explicitly:

```bash
uv run python -m benchmarks.layout_optimizer_topology_corpus \
  --proposals 256 --seed 0 --seeds 4 --include-scale
```

Every run validates the supplied layout, optimizes through the same public API, validates the returned
layout, and rejects any result whose lexicographic `(relay count, area, wire length)` objective is
worse than its valid input. Multi-seed runs report best/worst objectives and median physical metrics.

Cheap CI tests validate the nontrivial structural fixtures themselves and preserve exact zero-budget
pass-through behavior. The 1k+ scale case and full stochastic optimization sweeps remain opt-in.

## Annealer observability

`benchmarks/layout_optimizer_observability.py` runs representative corpus cases through the opt-in
observed optimizer while preserving the production optimizer's deterministic artifact. It reports
proposal acceptance/rejection categories, implementation-vs-relay move mix, swaps, topology rebuilds,
best-objective stagnation, relay simplification causes, and deterministic relay-routing search work.

Use at least 257 proposals when you specifically want the scheduled coarse-retopology path to execute;
512 is a convenient inspection budget:

```bash
uv run python -m benchmarks.layout_optimizer_observability --proposals 512 --seed 0
```

Routing work is reported as relay-path search calls and priority-queue pops rather than elapsed time,
so it is suitable for cross-machine regression comparisons. Runtime remains useful as an
informational benchmark measurement, but it is not an optimizer contract. Relay deletions are split
into isolated removals, leaf removals, and degree-two bypasses, with an aggregate consistency check.

The observed optimizer is intentionally opt-in. Routine physical synthesis continues to use the
production annealer directly; CI checks that a fixed seed and proposal budget produce the same final
optimization result through both paths.

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
