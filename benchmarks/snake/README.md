# Snake benchmark

Snake is the repository's heavyweight end-to-end benchmark. It is intentionally kept out of
`examples/`: unlike the small semantic demonstrations, the full workload expands to thousands of
physical combinators, hundreds of thousands of routing relays, and a multi-minute compile.

It simultaneously stresses:

- frontend vector operations and a 256-lane framebuffer decoder;
- periodic state and inferred recurrence timing;
- bounded FIFO-like body history;
- physical lowering and signal allocation;
- physical-net synthesis;
- large-layout construction and relay routing;
- real Factorio blueprint serialization;
- external device integration through the movement detector and 16x16 lamp screen.

The full benchmark is **not** part of routine pytest/CI. Small semantic Snake tests remain in
`tests/integration/test_snake.py`; use this directory when evaluating whole-compiler or physical-layout
changes.

## Files

- `model.py` — the interactive 16x16 Snake workload.
- `generate.py` — heavyweight compile/blueprint runner with selectable layout strategies.
- `baselines.json` — machine-readable, in-game-validated milestone measurements.
- `README.md` — benchmark contract and manual acceptance procedure.

The layout algorithm and its constructive invariants are documented separately in
`docs/safe-folded-crossbar-layout.md`. Benchmark measurements belong here so later optimizer work does
not have to reconstruct results from chat logs or prose.

## Canonical benchmark configuration

Unless a comparison says otherwise, use:

```text
render_framebuffer       = true
logical_steps_per_move   = 1
optimize                 = false
layout                   = safe-folded-crossbar
```

Generate the compiled Snake blueprint with:

```bash
uv run python -m benchmarks.snake.generate --output snake-blueprint.txt
```

The command prints compiler progress, folded-layout preflight statistics, final relay count, state
period, required wire colors, and the compact front-panel marker positions.

Compilation time is useful diagnostic information but is machine/load dependent. Record semantic and
physical metrics as the durable baseline; timing may be stored as an informational observation.

## Current validated milestone

The current `main` milestone is `dense-safe-folded-v1`, merged as
`1bde1650a393d881bd04e275197ec39ed2245e10`.

```text
implementation combinators = 5,657
physical entities           = 5,668
physical groups             = 5,338
row tracks                  = red:62, green:38
entity rows                 = 13
entities per row            = 437
layout relays               = 246,476
extent                      = 1,554 x 1,544 tiles
state period                = 60 ticks
```

The accepted pre-density Snake baseline was:

```text
layout relays = 470,732
extent        = 3,004 x 2,792 tiles
```

The density milestone therefore reduced routing relays by about **47.6%** and bounding-box area by
about **71.4%**, while the full game continued to run correctly in Factorio. The authoritative numeric
records are in `baselines.json`.

When a later optimization is accepted, append a new named milestone to `baselines.json`; do not mutate
or overwrite an already validated historical entry.

## Generate the three in-game blueprints

Snake uses two reusable external devices plus the compiled benchmark:

```bash
uv run python -m factorio_circuit.devices.player_movement_detector
uv run python -m factorio_circuit.devices.lamp_screen
uv run python -m benchmarks.snake.generate --output snake-blueprint.txt
```

Place all three blueprints in game. The detector and screen each carry parallel red and green buses.
Connect exactly the colors printed by the Snake generator:

```text
movement detector  -- printed color --> INPUT movement
reset pulse source -- printed color --> INPUT reset
OUTPUT framebuffer -- printed color --> DISPLAY INPUT
```

`reset` is a scalar compiler input, so the runner also prints its allocated concrete signal. Drive that
signal nonzero for at least one Snake state occurrence, then return it to zero. Holding reset nonzero
keeps the game in its startup state. Leave the unused device-bus color unattached.

## Manual acceptance

A candidate physical/layout optimization is not accepted merely because the blueprint encodes. For the
full end-to-end benchmark, verify in Factorio that:

1. the framebuffer is electrically active;
2. the first movement gesture starts the game;
3. several cardinal/diagonal turns are accepted correctly;
4. food and body pixels render correctly;
5. growth works after eating food;
6. wall/self-collision behavior remains stable;
7. reset restores the complete initial state and the game can start again.

If a new geometry assumption is uncertain, make a tiny physical probe first rather than repeatedly
paying for the full Snake compile.

## Layout strategies

The default is `safe-folded-crossbar`:

```text
real combinators: deterministic serpentine rows, 3-tile center pitch
public I/O: clustered at the start of the first row
fold portals: deterministic packed boundary-local columns
row buses: interval-packed from actual endpoint + portal attachment intervals
RED local tracks: above each entity row
GREEN local tracks: below each entity row
bus track spacing: 1 tile on one integer coordinate phase
first bus offset: 3 tiles
relay hop pitch: 6 tiles
row-width sizing: actual physical-net cut crossings / portal cost
relay preflight: exact unique relay-site count
placement search: none
routing search: none
retries: none
```

The canonical linear rollback/reference path is:

```bash
uv run python -m benchmarks.snake.generate \
  --linear-safe-layout \
  --output snake-linear.txt
```

Other diagnostic layouts remain available:

```bash
uv run python -m benchmarks.snake.generate --greedy-layout
uv run python -m benchmarks.snake.generate --net-aware-layout
uv run python -m benchmarks.snake.generate --row-layout
```

`--greedy-layout` and `--net-aware-layout` also accept `--corridor-width`, `--target-fill`, and
`--layout-retries`.

## Controls, startup, and reset

The game stays frozen at `(8, 8)` until the movement detector produces its first direction signal. The
first gesture may choose any cardinal direction. Cardinal detector regions request that direction
directly; diagonal regions act as perpendicular turns. Exact 180-degree reversals are rejected after
the game starts.

A legal direction gesture is queued until the next movement boundary, so the player need not remain in
a detector region until the snake advances.

A nonzero `reset` restores the game state atomically:

```text
head position       -> (8, 8)
direction           -> east reference / neutral startup
queued direction    -> neutral
score               -> 0
length              -> 1
dead                -> 0
started             -> 0
move divider phase  -> 0
body position FIFO  -> empty
body pixel FIFO     -> empty
food                -> first deterministic cell (11, 8)
```

Reset has priority over gameplay updates on the same logical occurrence.

For slower gameplay, add a logical divider:

```bash
uv run python -m benchmarks.snake.generate \
  --steps-per-move 2 \
  --output snake-blueprint.txt
```

The real-time move interval is the inferred state-domain period multiplied by this divider.

## Workload model

Food placement is deterministic: score `s` maps to

```text
((73 * s + 139) mod 256) + 1
```

The odd multiplier permutes all 256 cells before repeating. Maximum length is 16. The body uses two
parallel bounded histories: scalar cell identifiers for collision checks and one-hot framebuffer
vectors for rendering.

The framebuffer decoder uses one shared 256-lane pixel ROM. Pixel lane `(x, y)` stores
`cell_id(x, y) + 1`; subtracting the runtime cell ID from the whole vector and filtering count `1`
selects the corresponding one-hot pixel vector. This avoids a 256-way coordinate-comparison tree.

Moving into the current tail cell is legal when that tail vacates on the same move. Wall or self
collision latches the game dead and freezes movement. The full framebuffer renders the dead snake in
red.

For cheap semantic tests, call:

```python
build_snake_circuit(render_framebuffer=False)
```

That preserves the game-state logic while omitting pixel-history state and the expensive renderer.
