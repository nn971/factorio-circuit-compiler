# Snake prototype

`examples/snake.py` contains the first interactive Snake game model built for the external movement
detector and 16x16 packed-RGB lamp screen. `examples/snake_blueprint.py` is the recommended first
in-game generator.

The first-playtest generator deliberately prefers routing robustness over compactness. It uses the
deterministic greedy seed of the net-aware placer with zero optimization iterations, frequent routing
corridors, and deterministic retries that spend progressively more map area if routing fails.

## Generate the three blueprints

```bash
uv run python -m factorio_circuit.devices.player_movement_detector
uv run python -m factorio_circuit.devices.lamp_screen
uv run python -m examples.snake_blueprint
```

The Snake generator prints compiler progress, a short synthesis summary, and the exact synthesized wire
color required for the `movement` input and `framebuffer` output to stderr. The final importable
blueprint string alone is printed to stdout, so it may be redirected directly to a file.

Place all three blueprints in game. The detector and screen each carry parallel red and green buses.
Connect exactly the color printed by the Snake generator:

```text
movement detector  -- printed color -->  INPUT movement
OUTPUT framebuffer -- printed color -->  DISPLAY INPUT
```

Leave the other device-bus color unattached. The screen deliberately contains no power-distribution
entities.

## First-playtest layout policy

The default Snake physical placement is intentionally spacious:

```text
strategy            = net-aware greedy seed
optimization steps  = 0
computation block   = 8 x 8 tiles
initial corridor    = 4.0 tiles
initial target fill = 0.60
attempts             = 4
retry fill scale     = 0.8
```

A failed routing attempt rebuilds the placement deterministically with more routing space. The default
sequence is approximately:

```text
attempt    target fill    corridor width
1          0.600          4.00
2          0.480          5.00
3          0.384          6.25
4          0.307          7.81
```

No annealing or random optimization is performed in these attempts. The retry exists because a
placement that is cheap to compute can still leave too little collision-free space for relay chains.

The initial policy can be adjusted from the CLI:

```bash
uv run python -m examples.snake_blueprint --corridor-width 6
uv run python -m examples.snake_blueprint --target-fill 0.45
uv run python -m examples.snake_blueprint --layout-retries 6
```

These options can be combined. For example, a deliberately very spacious diagnostic build is:

```bash
uv run python -m examples.snake_blueprint \
    --corridor-width 8 \
    --target-fill 0.40 \
    --layout-retries 6
```

The old one-dimensional baseline remains available only for diagnosis:

```bash
uv run python -m examples.snake_blueprint --row-layout
```

It is not recommended for Snake because trivial row placement can create a much harder routing problem.
The full iterative net-aware optimizer remains available for later layout-quality testing:

```bash
uv run python -m examples.snake_blueprint --net-aware-layout
```

The deterministic search-free safe layout described in `layout-safe-fallback-plan.md` is a later
milestone. The spacious greedy retries are an intermediate robustness measure, not the final guarantee.

## Controls and startup

The game stays frozen at the center until the movement detector produces its first direction signal.
That first gesture starts the game and may choose any cardinal direction, including west; this makes it
safe to place, wire, and power the three blueprints before stepping into the controller.

The movement detector exposes the eight mutually exclusive compass-arrow virtual signals. Snake moves
on the four cardinal neighbours. Cardinal detector regions request that direction directly. Diagonal
regions act as turns: while travelling horizontally, NE/NW request north and SE/SW request south;
while travelling vertically, NE/SE request east and NW/SW request west. Exact 180-degree reversals are
ignored after the game has started.

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

The first prototype waits at `(8, 8)` with length one. Food placement is deterministic but cheap to
compute: score `s` maps to cell

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
  lamp path before adding richer gameplay or device composition helpers;
- the current routing path is still heuristic and may fail; the planned safe fallback will provide the
  later construction-by-design guarantee.

The semantic state machine can be built without the framebuffer renderer by calling
`build_snake_circuit(render_framebuffer=False)`. Contract tests use that form for most game-state
checks. The physical smoke test compiles the full renderer using the same spacious deterministic greedy
policy as the first-playtest generator.
