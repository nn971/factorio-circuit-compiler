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
the current in-game test and expose one `Level[Vector]`-style circuit bus. Each direction uses the
matching base-game compass-arrow virtual signal:

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

The eight lamps from the tested prototype remain as direction indicators. The sensor network is wired
in parallel on both red and green circuit colors, so a compiled consumer can attach using whichever
color its synthesized `INPUT movement` port requires. Connect only that one matching color.

## 16x16 packed-RGB lamp screen

Generate the screen with:

```bash
uv run python -m factorio_circuit.devices.lamp_screen
```

The command prints one importable blueprint string containing one empty labelled input terminal and a
16x16 grid of lamps. Power distribution is deliberately not part of the device blueprint.

The screen consumes one persistent packed framebuffer vector. Pixel coordinates use a top-left origin
and row-major lane numbering:

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
deterministic. It first exhausts 81 verified ordinary base-game virtual signals reserved specifically
for display pixels: symbols, punctuation, shapes, compass and miscellaneous arrows, and pictographs.
Those lanes are deliberately disjoint from `DEFAULT_VIRTUAL_SIGNAL_POOL`, leaving the compiler's
small stable virtual-signal allocator free for intermediate arithmetic while a program drives the
framebuffer. Selector pseudo-signals such as `signal-each`, `signal-everything`, and `signal-anything`
are excluded. The remaining 175 framebuffer lanes use base-game item/recipe/entity signal identities.
Programs should normally use `pixel_signal(x, y)` rather than depend on the concrete catalogue ordering
directly.

All 256 lamps are connected by parallel red and green short-hop serpentine networks. The empty constant
combinator to the left of the top row is labelled `DISPLAY INPUT: 16x16 packed-RGB framebuffer`. Connect
only the wire color used by the compiled `OUTPUT framebuffer` port; the unused parallel color remains
empty, so RGB counts are not doubled.

## Open design note: external-device wire direction convention

A promising general convention for reusable external devices is:

```text
GREEN = signals entering the device
RED   = signals leaving the device
```

The motivation is to make device boundaries visually obvious and mechanically checkable. A caller can
infer signal direction from wire color without knowing the device's internal entity configuration, and
the device implementation can use isolation combinators internally whenever the raw Factorio entity
cannot satisfy that convention directly.

The current assembler-device work already fits this idea naturally: recipe, enable, and requester
demand are commands into the device, while ingredients, working/finished status, requester contents,
and provider contents are observations emitted by the device.

This is intentionally recorded as an **open design principle rather than a finalized ABI rule**. Before
making it mandatory, revisit at least:

- devices that are naturally one-way and currently expose both colors for color-agnostic attachment;
- devices with several independent input/output buses;
- Factorio entities whose circuit behavior mixes commands and observations on one physical connector;
- whether protocol adapters should always enforce the convention, or whether some low-level raw-device
  generators should remain color-agnostic;
- how the convention should interact with physical synthesis, named ports, and future device composition.

If adopted, the compiler/device boundary should encode input/output direction explicitly and validate
that exported physical ports obey this color convention rather than relying on documentation alone.

## Organization rule

Add future fixed peripherals as sibling generator modules under `factorio_circuit.devices`. Shared raw
blueprint encoding belongs in `devices/_blueprint.py`; compiler-owned logical/physical lowering stays
in the existing frontend/lowering/synthesis pipeline.
