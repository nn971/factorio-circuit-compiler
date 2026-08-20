# External Level sampling policies

Physical compilation can choose when a live external Level observation is sampled within one
logical occurrence.  This policy applies uniformly to ordinary scalar/vector inputs and semantic
scalar/vector oracles; both enter Level physical lowering as live circuit-network boundaries.

```python
from factorio_circuit import SamplingPolicy

result = circuit.compile(
    sampling_policy=SamplingPolicy.ALAP,
    ...,
)
```

## `BEGINNING_OF_STEP`

`SamplingPolicy.BEGINNING_OF_STEP` is the compatibility default.  The value visible at physical
phase zero is treated as an exact logical snapshot.  If a consumer is scheduled several physical
ticks later, lowering must preserve that old token, potentially by inserting arithmetic identity
combinators.

```text
external value @ phase 0
        |
        +-- delay -- delay -- delay --> consumer @ phase 3
```

This remains the appropriate policy when the environment may change during the logical occurrence
and all consumers are required to agree on the beginning-of-occurrence snapshot.

## `ALAP`

`SamplingPolicy.ALAP` treats the external boundary as a live Level source.  When ALAP scheduling asks
to align a phase-zero external value to a later consumer phase, lowering observes the live physical
net at that later phase instead of transporting the phase-zero token.

```text
external live net --------------------> sample/consume @ phase 3
```

The relocation itself has zero combinator cost.  Different consumers may therefore observe the same
external source at different physical phases inside one logical occurrence.  This is intentional and
is the main use case for a player-movement detector: direction need not be captured at the beginning
of a long Snake reaction and delayed through the whole combinational cone.

ALAP sampling applies to:

- scalar ordinary inputs;
- whole-vector ordinary inputs;
- scalar oracle observations;
- whole-vector oracle observations;
- scalar lane views projected from a live external vector.

It does **not** reinterpret an explicit logical sample/reindexing.  A source already carrying a
nonzero logical/physical sample offset keeps exact transport behavior.  It also does not apply to
Event compilation yet.

## Snake benchmark

`benchmarks.snake.generate` defaults to ALAP sampling on the `agent/alap-input-sampling` experiment
branch.  Compare both policies with otherwise identical compilation:

```bash
uv run python -m benchmarks.snake.generate \
  --sampling-policy beginning-of-step \
  --output snake-beginning.txt

uv run python -m benchmarks.snake.generate \
  --sampling-policy alap \
  --output snake-alap.txt
```

The randomized-food Snake model and selector provider are identical in both runs.  Therefore the
combinator-count difference directly measures physical transport removed by the ALAP observation
policy.
