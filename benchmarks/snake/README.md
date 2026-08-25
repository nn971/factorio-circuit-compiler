# Snake benchmark

Snake is the repository's heavyweight end-to-end benchmark. It stresses periodic state, 256-lane
vector logic, physical synthesis, safe large-scale layout, blueprint serialization, and external
movement/display devices.

The full benchmark is intentionally outside routine pytest/CI. Cheap semantic coverage stays in
`tests/integration/test_snake.py`; whole-framebuffer and in-game validation remain explicit acceptance
steps.

## Workload

`model.py` implements the deterministic-food 16x16 Snake used by the temporal mapper. The body uses two
256-lane vectors:

```text
body_ttl   per-pixel remaining lifetime
body_mask  per-pixel 0/1 occupancy
```

The head coordinates, direction, queued direction, score, dead flag, and started flag are periodic
state. Food cell `s` is:

```text
((73 * s + 139) mod 256) + 1
```

The odd multiplier visits all 256 cells before repeating. The framebuffer decoder uses a shared
256-lane pixel-ID ROM rather than a 256-way scalar comparator tree.

`random_model.py` is a separate Random Input selector/oracle variant. The accepted mapped blueprint in
this milestone deliberately uses `model.py`, so the physical workload exactly matches the recurrence
solved by the mapper.

## Important files

- `model.py` — deterministic full Snake recurrence.
- `random_model.py` — Random Input selector/oracle variant.
- `semantic_acceptance.py` — opt-in full semantic acceptance.
- `generate.py` — production compiler blueprint generator.
- `census.py` — production Abstract Physical census.
- `analyze_mapping.py` — temporal mapper diagnostics and full recurrence solve.
- `lower_mapping.py` — mapped `RealizationPlan -> AbstractPhysicalCircuit` accounting check.
- `generate_mapping.py` — accepted joint-mapper blueprint generator.
- `baselines.json` — append-only benchmark history.

Historical temporal/transport probe scripts remain available for regression and comparison, but new
Snake optimization work should use the `mapping` commands above.

To benchmark the general-purpose physical optimizer from a complete safe-folded routed seed rather
than from the annealer's constructive seed, run:

```bash
uv run python -m benchmarks.snake.generate \
  --anneal-safe-folded-seed \
  --annealing-iterations 512 \
  --layout-seed 0 \
  --layout-retries 1 \
  --census \
  --output /tmp/snake-generic-front-panel-512-seed0.txt
```

This mode records input/output implementation count, relay count, occupied area, routed wire
length, proposal budget, runtime, and any rejected optimization-phase diagnostics. The production
optimizer receives only the concrete `Layout` and generic physical constraints; safe-folded is
used solely to construct this benchmark input. Public marker constants are fixed as a recognizable
front panel: reset and movement are the first two labeled constants at `(0,0)` and `(3,0)`, and all
implementation/routing geometry remains on the circuit-facing side of the marker row.

## Validation tiers

Routine semantic regression:

```bash
uv run pytest tests/integration/test_snake.py
```

Full semantic acceptance when framebuffer/state behavior changes:

```bash
uv run python -m benchmarks.snake.semantic_acceptance
```

Production pre-synthesis census:

```bash
uv run python -m benchmarks.snake.census --deep-delays
```

Mapped recurrence solve:

```bash
uv run --with 'ortools>=9.14,<10' \
  python -m benchmarks.snake.analyze_mapping \
  --period 60 \
  --solve-full-state \
  --compare-private \
  --time-limit 300 \
  --workers 8
```

Mapped physical accounting:

```bash
uv run --with 'ortools>=9.14,<10' \
  python -m benchmarks.snake.lower_mapping \
  --period 60 \
  --time-limit 300 \
  --workers 8
```

Mapped in-game blueprint:

```bash
uv run --with 'ortools>=9.14,<10' \
  python -m benchmarks.snake.generate_mapping \
  --period 60 \
  --time-limit 300 \
  --workers 8
```

The mapped generator writes `snake-mapped-blueprint.txt` by default and prints the concrete reset
signal plus required red/green wire colors for movement, reset, and framebuffer connections.

## Current accepted mapped milestone

The full deterministic recurrence at `P=60` is now accepted end to end.

The joint mapper proved:

```text
operations            213
state cells             36
shared periodic commit   3
transport               175
---------------------------
mapped objective        427
```

The all-private comparison is 605 total with 353 transport, so the selected delay bus saves 178
modeled entities.

The selected bus contains:

```text
29 scalar lanes
middle [44, 118)
74 shared middle stages
70 interfaces
```

Physical lowering accounts exactly for all known hardware outside the current solver objective:

```text
mapped objective               427
fixed semantic sources           8
Select-internal preservation    20
coherent framebuffer HOLD        2
                                ---
implementation                  457
unexplained gap                   0
```

The two-combinator framebuffer HOLD is required because internal state cells are intentionally allowed
to commit at different phases while the lamp display observes its circuit network continuously. It
publishes one coherent frame per occurrence and hides intermediate settling states.

The resulting blueprint was tested in Factorio and the Snake ran perfectly, including synchronized
head/body rendering.

The mapper remains separate from production `compile_circuit()` in this milestone; this acceptance
validates the opt-in route rather than silently replacing the production compiler.

## Previous accepted production milestones

`baselines.json` retains the complete append-only history. Important reference points include:

```text
dense-safe-folded-v1   5,657 implementation combinators
settling-alap-v1       1,131 implementation combinators
random-food-alap-v1      698 implementation combinators
mapped-state-bus-v1      457 implementation combinators
```

The failed live-source bus experiments are also retained in the baseline file because they document
why free re-observation requires an explicit source/provider contract.

## In-game wiring

Generate the two reusable external devices and the Snake blueprint:

```bash
uv run python -m factorio_circuit.devices.player_movement_detector
uv run python -m factorio_circuit.devices.lamp_screen
uv run --with 'ortools>=9.14,<10' \
  python -m benchmarks.snake.generate_mapping \
  --period 60 --time-limit 300 --workers 8
```

Then connect exactly the colors printed by the generator:

```text
movement detector  -> INPUT movement
reset pulse source -> INPUT reset on the printed scalar signal
OUTPUT framebuffer -> lamp-screen input
```

A nonzero reset restores the startup state. Return reset to zero before playing.

Manual acceptance should cover startup, cardinal/diagonal turns, eating/growth, wall and self
collision, reset/restart, and visually coherent framebuffer updates.

## Safe layout

The canonical reliable layout remains `safe-folded-crossbar`: deterministic serpentine rows,
row-local red/green tracks, packed fold portals, and search-free reach-safe relay construction.

The production generator exposes the older layout diagnostics and rollback/reference modes. The mapped
generator intentionally defaults to the same safe-folded backend so temporal-mapping correctness is
not confounded with a new placement/routing experiment.

For the mapped generator, the one-row fallback is:

```bash
uv run --with 'ortools>=9.14,<10' \
  python -m benchmarks.snake.generate_mapping \
  --period 60 --linear-safe-layout
```

Layout topology is documented separately in `docs/safe-folded-crossbar-layout.md`.

## Controls

The game starts on the first legal movement gesture. Cardinal detector regions request the matching
direction. Diagonal regions request the perpendicular turn appropriate to the current orientation.
Exact 180-degree reversal is rejected after startup.

A reset restores:

```text
head position       -> (8, 8)
direction           -> east reference
queued direction    -> neutral
score               -> 0
length              -> 1
dead                -> 0
started             -> 0
body_ttl             -> empty
body_mask            -> empty
food                 -> first deterministic cell (11, 8)
```

Reset has priority over gameplay updates on the same logical occurrence.
