# Snake prototype

`examples/snake.py` contains the first interactive Snake game model built for the external movement
detector and 16x16 packed-RGB lamp screen. `examples/snake_blueprint.py` is the recommended first
in-game generator: it defaults to deterministic row placement and one unpacked synthesis so blueprint
generation does not turn the first gameplay test into a layout-optimization benchmark.

## Generate the three blueprints

```bash
uv run python -m factorio_circuit.devices.player_movement_detector
uv run python -m factorio_circuit.devices.lamp_screen
uv run python -m examples.snake_blueprint
```

The Snake generator prints a short synthesis summary, the exact synthesized wire color required for
the `movement` input and `framebuffer` output, and then the compiled circuit blueprint string.

Place all three blueprints in game. The detector and screen each carry parallel red and green buses.
Connect exactly the color printed by the Snake generator:

```text
movement detector  -- printed color -->  INPUT movement
OUTPUT framebuffer -- printed color -->  DISPLAY INPUT
```

Leave the other device-bus color unattached. The screen deliberately contains no power-distribution
entities.

For later physical-synthesis stress testing, the generator can opt into vector packing and/or the full
net-aware placer:

```bash
uv run python -m examples.snake_blueprint --optimize
uv run python -m examples.snake_blueprint --net-aware-layout
```

## Controls

The movement detector exposes the eight mutually exclusive compass-arrow virtual signals. Snake moves
on the four cardinal neighbours. Cardinal detector regions request that direction directly. Diagonal
regions act as turns: while travelling horizontally, NE/NW request north and SE/SW request south;
while travelling vertically, NE/SE request east and NW/SW request west. Exact 180-degree reversals are
ignored.

A legal direction gesture is queued until the next movement boundary, so the player does not need to
remain inside a gate sensor until the snake advances. Reverse rejection is checked against the most
recent direction that actually moved the snake; multiple gestures between moves therefore cannot
smuggle in a net 180-degree reversal.

By default the snake moves once per compiler-inferred periodic state occurrence. If that is too fast in
game, add a logical divider when generating the circuit:

```bash
uv run python -m examples.snake_blueprint --steps-per-move 2
```

The real-time move interval is the inferred state-domain period multiplied by this divider. The script
reports the inferred state period after compilation.

## Game model

The first prototype starts at `(8, 8)` moving east with length one. Food placement is deterministic but
cheap to compute: score `s` maps to cell

```text
((73 * s + 139) mod 256) + 1
```

so the first food is at `(11, 8)` and the odd multiplier permutes the 256 board cells before repeating.
Each food increments score and length until the fixed maximum length of 16 is reached. The body is
represented by two parallel bounded FIFOs: scalar cell identifiers support self-collision checks, while
one-hot framebuffer vectors support rendering. Collision reductions are balanced to avoid making the
state recurrence unnecessarily deep.

The framebuffer decoder uses one shared 256-lane constant pixel ROM. Pixel lane `(x, y)` stores
`cell_id(x, y) + 1`; subtracting the runtime cell ID from the whole vector and filtering count `1`
produces the corresponding one-hot pixel vector. The same operation renders the head and food and
feeds the body-pixel FIFO, avoiding a 256-way tree of scalar coordinate comparisons.

Moving into the cell occupied by the current tail is legal when that tail vacates on the same move.
Wall or self collision latches the game into a dead state and freezes movement. The snake turns red in
the full framebuffer build. A food target temporarily hidden under the snake becomes visible again as
soon as the occupied cell is vacated.

## Current deliberate limitations

- food placement is deterministic rather than random;
- maximum snake length is 16;
- there is no restart/reset input yet;
- an optional game-speed divider is implemented as state, rather than as a separately declared game
  clock;
- the first prototype is intended to validate the complete input -> state -> packed framebuffer ->
  lamp path before adding richer gameplay or device composition helpers.

The semantic state machine can be built without the framebuffer renderer by calling
`build_snake_circuit(render_framebuffer=False)`. Contract tests use that form for most game-state
checks. The physical smoke test compiles the full renderer with deterministic row placement; the
net-aware placer remains an explicit stress-test mode rather than part of the fast contract test.
