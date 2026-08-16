# Snake prototype

`examples/snake.py` contains the first interactive Snake game model built for the external movement
detector and 16x16 packed-RGB lamp screen. `examples/snake_blueprint.py` is the recommended first
in-game generator.

The first-playtest generator uses `safe-folded-crossbar` by default. This is an experimental,
search-free placeability refinement of the canonical linear `safe-crossbar`. The linear strategy is
preserved unchanged and can be selected immediately with `--linear-safe-layout` if a folded-layout
probe exposes a geometry bug.

## Generate the three blueprints

```bash
uv run python -m factorio_circuit.devices.player_movement_detector
uv run python -m factorio_circuit.devices.lamp_screen
uv run python -m examples.snake_blueprint --output snake-blueprint.txt
```

The Snake generator prints compiler progress, folded-layout preflight statistics, final relay count,
and the synthesized interface details for `reset`, `movement`, and `framebuffer` to stderr. It also
prints the relative marker positions of those three front-panel ports. `--output` writes the encoded
blueprint directly to a file instead of sending a potentially large string to the terminal.

Place all three blueprints in game. The detector and screen each carry parallel red and green buses.
Connect exactly the colors printed by the Snake generator:

```text
movement detector  -- printed color -->  INPUT movement
reset pulse source -- printed color -->  INPUT reset
OUTPUT framebuffer -- printed color -->  DISPLAY INPUT
```

`reset` is a scalar compiler input, so the generator also prints the concrete signal lane allocated to
it. Drive that signal nonzero for at least one Snake state occurrence, then return it to zero. Holding
it nonzero simply keeps the game in its initial state.

Leave the unused device-bus color unattached. The screen deliberately contains no power-distribution
entities.

## Layout strategies

The default is:

```text
safe-folded-crossbar
    real combinators: deterministic serpentine rows
    public I/O: clustered at the start of the first row
    fold portals: deterministic boundary-local columns
    row buses: interval-packed from actual endpoint + portal attachment intervals
    RED local tracks: above each entity row
    GREEN local tracks: below each entity row
    bus track spacing: 2 tiles
    relay hop pitch: 6 tiles
    relay preflight cap: 1,000,000
    maximum preflight dimension: 4,096 tiles
    placement search: none
    routing search: none
    retries: none
```

A cross-row physical net may use different local track numbers on adjacent rows. A deterministic
vertical fold stitch connects those bus heights. Track reuse is decided only after portal extensions
are included in the physical row segment interval; this is required because the first folded draft
incorrectly reused global linear track identities and full Snake exposed a relay-site collision.

The canonical linear rollback/reference path is:

```bash
uv run python -m examples.snake_blueprint \
  --linear-safe-layout \
  --output snake-linear.txt
```

That path remains the already demonstrated one-row `safe-crossbar`. It is electrically constructive and
search-free, but full Snake is extremely wide.

Older physical-layout paths remain available for diagnosis and the later routing-optimization
milestone:

```bash
uv run python -m examples.snake_blueprint --greedy-layout
uv run python -m examples.snake_blueprint --net-aware-layout
uv run python -m examples.snake_blueprint --row-layout
```

`--greedy-layout` and `--net-aware-layout` still accept `--corridor-width`, `--target-fill`, and
`--layout-retries`. Those options are ignored by both constructive safe-crossbar strategies.

## Controls, startup, and reset

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

A nonzero `reset` restores the entire game state atomically:

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

Reset has priority over gameplay updates on the same logical occurrence. For accumulator state this is
implemented with the existing clear control, which physically suppresses same-occurrence adds. For
freeze state, the current physical lowerer permits exactly one periodic set source, so reset is folded
into that source: the update value is gated to the empty vector while the combined update/reset control
forces the register to accept it. A reset asserted together with a movement gesture therefore leaves
the game at startup and does not queue that gesture; a fresh gesture after reset starts the next game.

By default the snake moves once per compiler-inferred periodic state occurrence. If that is too fast in
game, add a logical divider when generating the circuit:

```bash
uv run python -m examples.snake_blueprint --steps-per-move 2 --output snake-blueprint.txt
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
- an optional game-speed divider is implemented as state, rather than as a separately declared game
  clock;
- both safe crossbars are correctness/placeability baselines rather than final optimized routing;
- the first prototype is intended to validate the complete input -> state -> packed framebuffer ->
  lamp path before adding richer gameplay or device composition helpers.

The semantic state machine can be built without the framebuffer renderer by calling
`build_snake_circuit(render_framebuffer=False)`. Contract tests use that form for most game-state
checks. The physical smoke test compiles the full renderer using the folded safe-crossbar path.
