# External device generators

Fixed in-game peripherals live under `factorio_circuit.devices`.

These generators build physical Factorio blueprints directly. They are intentionally separate from the
compiler pipeline: a device may expose circuit-network signals that a compiled circuit consumes or
drives, while its internal game entities do not pass through semantic IR or physical synthesis.

## Player movement detector

Generate the current eight-way player movement sensor with:

```bash
uv run python -m factorio_circuit.devices.player_movement_detector
```

The command prints one importable blueprint string.

The physical geometry is the tested eight-gate prototype. The gate sensors are mutually exclusive in
the current in-game test and expose one shared `Level[Vector]`-style circuit bus. Each direction uses
the matching base-game compass-arrow virtual signal:

```text
up-arrow         = N
up-right-arrow   = NE
right-arrow      = E
down-right-arrow = SE
down-arrow       = S
down-left-arrow  = SW
left-arrow       = W
up-left-arrow    = NW
```

The eight lamps from the tested prototype remain as direction indicators and are connected to the same
green circuit network. A consumer may connect to any of those indicator lamps to read the complete
one-hot vector.

## 16x16 packed-RGB lamp screen

Generate the screen with:

```bash
uv run python -m factorio_circuit.devices.lamp_screen
```

The command prints one importable blueprint string containing one empty labelled input terminal and a
16x16 grid of lamps. Power distribution is deliberately not part of the device blueprint.

The screen consumes one persistent packed framebuffer vector on a single green circuit network. Pixel
coordinates use a top-left origin and row-major lane numbering:

```text
(0, 0) -----------------> x
  |
  |
  v
  y

lane_index = y * 16 + x
```

Each pixel owns one fixed Factorio `SignalId`. Its signed 32-bit circuit count is interpreted by the
lamp's packed-RGB mode as `0xRRGGBB`; zero therefore means black. The public helpers are:

```python
from factorio_circuit.devices import pixel_signal, rgb

lane = pixel_signal(3, 5)
green = rgb(0, 255, 0)
```

`PIXEL_SIGNALS` is the complete 256-lane table used by the generated blueprint. The table is fixed and
deterministic. It first exhausts a screen-local catalogue of 132 verified ordinary base-game virtual
signals: the compiler's small stable allocation pool plus additional symbols, punctuation, shapes,
compass and miscellaneous arrows, and pictographs. Selector pseudo-signals such as `signal-each`,
`signal-everything`, and `signal-anything` are excluded. Only the remaining framebuffer lanes fall back
to base-game item/recipe/entity signals. Programs should normally use `pixel_signal(x, y)` rather than
depend on the concrete catalogue ordering directly.

All 256 lamps are wired into one short-hop serpentine green network. The empty constant combinator to
the left of the top row is labelled `DISPLAY INPUT: 16x16 packed-RGB framebuffer` and is the preferred
attachment point for compiled output.

## Organization rule

Add future fixed peripherals as sibling generator modules under `factorio_circuit.devices`. Shared raw
blueprint encoding belongs in `devices/_blueprint.py`; compiler-owned logical/physical lowering stays
in the existing frontend/lowering/synthesis pipeline.
