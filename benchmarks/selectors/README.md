# Selector-combinator probes

These small blueprints isolate target selector behavior before it is embedded into larger benchmark
circuits.

## Random Input

Generate a one-selector Random Input probe:

```bash
uv run python -m benchmarks.selectors.random_input_probe
```

Use `--update-interval N` to change the selector's update interval and `--output PATH` to choose the
blueprint file. The command prints the synthesized wire colors for the two public boundaries:

```text
constant combinator candidate vector -> INPUT candidates
OUTPUT choice -> observation network
```

Feed several nonzero signals into `candidates`. The selector should pass through one random input
signal and update it at the configured game-tick interval. A single candidate should be passed once
the interval threshold is met; removing it before then should leave no output.

The deterministic physical simulator deliberately does not invent Random Input outcomes. Reference
semantics use scripted oracle traces; actual nondeterministic behavior is accepted in Factorio.

The production random-food Snake (`python -m benchmarks.snake.generate`) already uses this same
`RandomSignalOracleProvider`. The temporal-mapper Snake still uses deterministic food because its
mapping problem does not yet model the provider dependency from the candidate-mask computation to
the oracle output. Adding that one-tick provider edge is a separate mapper milestone.
