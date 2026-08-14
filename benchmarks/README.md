# Benchmarks

The repository already uses representative large circuits as optimizer and layout benchmarks.
Current benchmark families include:

- bitonic sorting networks from `examples/sorting_network.py`;
- Walsh-Hadamard transforms from `examples/walsh_hadamard.py`;
- stateful vector structures such as the FIFO/stack and autonomous-market controller when timing or
  state realization is under test.

`tests/integration/test_layout_benchmark_examples.py` verifies semantic results, compilation of
representative sizes, real blueprint serialization, and selected combinator-count regressions.
Physical synthesis also exposes `placement_metrics(...)` for geometry-oriented comparisons.

When comparing compiler strategies, record at least:

- physical combinator count;
- output phase / latency;
- inferred state-domain periods when applicable;
- placement disconnected-component count;
- estimated relay count and MST wire length;
- realized footprint and actual routed relay/wire counts when the final `Layout` is available;
- compiler/synthesis runtime for larger parameterized examples.

Keep benchmark assertions focused: exact counts are useful for deliberate regression guards, while
exploratory optimizer comparisons should report metrics without turning every current heuristic into
an architectural contract.
