# Snake benchmark

Snake is the repository's heavyweight end-to-end benchmark. It is intentionally kept out of
`examples/`: unlike the small semantic demonstrations, the full workload expands to a large physical
circuit, substantial routing, and a multi-minute compile.

It simultaneously stresses:

- frontend vector operations and a 256-lane framebuffer decoder;
- periodic state and inferred recurrence timing;
- bounded FIFO-like body history;
- physical lowering and signal allocation;
- physical-net synthesis;
- large-layout construction and relay routing;
- real Factorio blueprint serialization;
- external device integration through the movement detector and 16x16 lamp screen.

The full benchmark is **not** part of routine pytest/CI. `tests/integration/test_snake.py` keeps cheap
gameplay/state coverage with `render_framebuffer=False` plus a small stateless framebuffer-decoder
check. Full framebuffer/state semantic simulation is an explicit benchmark acceptance task.

## Files

- `model.py` — the interactive 16x16 Snake workload.
- `semantic_acceptance.py` — opt-in full framebuffer/state semantic acceptance checks.
- `generate.py` — heavyweight compile/blueprint runner with selectable layout strategies.
- `census.py` — pre-synthesis Abstract Physical IR census runner, including residual delay-graph analysis.
- `analyze_mapping.py` — implementation-neutral periodic output-cone technology-mapping diagnostic.
- `baselines.json` — machine-readable, in-game-validated milestone measurements.
- `README.md` — benchmark contract and manual acceptance procedure.

The layout algorithm and its constructive invariants are documented separately in
`docs/safe-folded-crossbar-layout.md`. The accepted temporal-lowering milestone is documented in
`docs/temporal-lowering-milestone.md`. Benchmark measurements belong here so later optimizer work does
not have to reconstruct results from chat logs or prose.

## Validation tiers

Routine regression coverage stays under pytest:

```bash
uv run pytest tests/integration/test_snake.py
```

That path intentionally does **not** semantically simulate the full 16x16 framebuffer state graph.
When a change touches pixel-history state, reset/render interactions, or the full framebuffer
expression, run the opt-in semantic acceptance instead:

```bash
uv run python -m benchmarks.snake.semantic_acceptance
```

For lowering/synthesis changes, use the census before paying for placement/routing:

```bash
uv run python -m benchmarks.snake.census --deep-delays
```

For temporal-technology-mapping work, the post-update output cone can be analyzed without changing
the production compiler path. Supply the logical period explicitly. The current accepted benchmark
period is 60 ticks, so this is the canonical comparison command:

```bash
uv run --with 'ortools>=9.14,<10' \
  python -m benchmarks.snake.analyze_mapping \
  --period 60 \
  --compare-private
```

`analyze_mapping` does not call the established state-timing analyzer. The supplied period is a
throughput constraint only: offset-one state reads become stable sources on `[60, 120)`, outputs are
demanded at tick 119, and the joint mapper chooses all combinational phases itself. It currently maps
only the post-update output/rendering cone, not state-transition hardware, so its result is diagnostic
rather than an accepted replacement blueprint.

Only use the full blueprint build when the change needs whole-compiler/layout acceptance:

```bash
uv run python -m benchmarks.snake.generate --output snake-blueprint.txt
```

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

## Abstract physical census

Before changing compiler optimization, inspect exactly what target lowering emitted:

```bash
uv run python -m benchmarks.snake.census
```

This stops before signal allocation, red/green assignment, physical-net coalescing, placement, and
routing. The report records implementation/annotation entity counts, arithmetic and decider mixes,
phase-alignment delays, state-realization roles, state-register families, abstract lane/conflict counts,
and physical-net endpoint-size distribution.

The role classification is diagnostic rather than a correctness contract. It recognizes stable
compiler-generated descriptions such as `phase alignment delay`, `vector phase alignment delay`, and
the current `AccumulatorReg`/`FreezeReg` implementation descriptions. Future optimizers must not depend
on those strings; timing/provenance metadata should be made explicit before rewrite legality uses it.

Useful comparison modes are:

```bash
# Current canonical workload.
uv run python -m benchmarks.snake.census

# Reconstruct residual exact-delay trunks and classify their sources/sinks.
uv run python -m benchmarks.snake.census --deep-delays

# Isolate core gameplay/state by removing pixel-history state and framebuffer rendering.
uv run python -m benchmarks.snake.census --no-framebuffer

# Measure what the existing lowering-level packing already achieves.
uv run python -m benchmarks.snake.census --optimize

# Machine-readable output for benchmark records or diffs.
uv run python -m benchmarks.snake.census --deep-delays --json
```

`--deep-delays` reconstructs the delay-only graph from abstract nets. It reports connected-component
sizes/depths, whether components branch or merge, and delay-weighted source/sink classes. This is the
preferred diagnostic when deciding whether residual phase delays represent duplicated transport,
startup machinery, external snapshots, or an unscheduled computation cone.

`--no-framebuffer` is especially useful before compiler optimization because Snake deliberately stores
both scalar body positions and one-hot body-pixel history. Comparing the two censuses tells us how much
of the target realization belongs to gameplay/state versus the current display strategy.

## Current validated milestone

The current accepted milestone is `settling-alap-v1`. The safe-folded full Snake produced by the
production validity-window settling + ALAP pipeline ran flawlessly in Factorio.

The accepted pre-synthesis census is:

```text
implementation combinators = 1,131
annotation entities         =    11
abstract entities total     = 1,142
abstract nets               = 1,006
phase delays                =   430
  scalar                    =   406
  vector                    =    24
computation                 =   453
state implementation        =   222
state period                = 60 ticks
```

The previous validated `dense-safe-folded-v1` milestone had:

```text
implementation combinators = 5,657
layout relays               = 246,476
extent                      = 1,554 x 1,544 tiles
state period                = 60 ticks
```

Thus the accepted temporal-lowering milestone reduced implementation combinators by about **80.0%**
without changing the Snake algorithm or clock period. Compared with the original 4,960 phase-delay
combinators, the final census contains 430, a reduction of about **91.3%**.

The acceptance run did not record a new relay/extent snapshot, so those fields are intentionally absent
from the new baseline rather than inferred. The authoritative append-only numeric records are in
`baselines.json`.

The residual deep census at acceptance is:

```text
total delays       = 430
components         = 32
linear components  = 32
branching          = 0
max component size = 80

source-weighted delays:
  computation      = 270
  clock/startup    = 80
  external input   = 80
```

The two external-input components are one `reset` scalar trunk and one `movement` vector trunk. The
largest 80-tick chain is intentional startup readiness feeding output HOLD. Remaining computation-side
padding is localized enough to defer until a later optimization pass.

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
uv run python -m benchmarks.snake.generate --annealing-layout
uv run python -m benchmarks.snake.generate --row-layout
```

`--annealing-layout` is an alias for the previous full `net-aware` policy: deterministic greedy
seeding, simulated annealing, deterministic relaxation, then collision-aware heuristic routing. Even
after ALAP reduced the benchmark to about 1.1k implementation combinators, this strategy remained too
slow for convenient Snake iteration. Treat annealer scalability as a separate placer problem; the
safe-folded strategy remains the canonical reliable layout.

`--greedy-layout` and `--net-aware-layout` / `--annealing-layout` also accept `--corridor-width`,
`--target-fill`, and `--layout-retries`.

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
started              -> 0
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

That preserves the game-state logic while omitting pixel-history state and the expensive renderer. For
the full renderer/state interaction, use `python -m benchmarks.snake.semantic_acceptance` instead of
putting a full-framebuffer simulation back into routine pytest.
