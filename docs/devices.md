# External device generators

Fixed in-game peripherals live under `factorio_circuit.devices`.

These generators build physical Factorio blueprints directly. They are intentionally separate from the
compiler pipeline: a device may expose circuit-network signals that a compiled circuit consumes or
drives, while its internal game entities do not pass through semantic IR or physical synthesis.

Reusable devices may also expose a typed `DeviceProtocol`: named input/output ports carry payload
shape, Level/Event modality, wire color, and an optional fixed scalar signal. `ExternalDeviceBlueprint`
binds those logical ports to concrete Factorio entity connectors. Exact-overlap composition is defined
by `docs/device-anchoring.md`; application-specific scheduling and accounting do not belong in the
device layer.

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

## Programmable speaker output

`ProgrammableSpeakerDevice` is the first Milestone F output peripheral. Its stable typed boundary is a
single GREEN Level-scalar input named `trigger` on fixed `signal-A`. The speaker's circuit condition is
`signal-A > 0`; thresholding, hysteresis, debouncing, pulse shaping, and application alarm policy belong
in compiled logic rather than inside the reusable device.

The physical device consists of a constant-combinator input dock and one programmable speaker. The dock
is the exact-overlap anchor, so a compiled output can be normalized to the stable GREEN `signal-A` ABI
without treating the speaker prototype itself as a special compiler anchor.

The generator exposes the current Factorio 2.1 speaker configuration directly: playback volume,
`local`/`surface`/`global` playback mode, polyphony, optional signal-controlled volume, optional
signal-value-as-pitch, stop-playing-sounds behavior, numeric instrument/note ids, and GUI/map alert
settings. Instrument/note ids deliberately remain numeric because their semantic catalogue is owned by
Factorio and may change across game versions.

Generate the standalone speaker with:

```bash
uv run python -m factorio_circuit.devices.programmable_speaker
```

Generate the compiled integration probe with:

```bash
uv run python examples/programmable_speaker_probe.py
```

The integration probe compiles an ordinary scalar signal, adapts the public output to GREEN `signal-A`,
and exact-overlap composes it with the speaker in one importable blueprint. The current compiled-anchor
boundary supports Level ports only, so F1 does not falsely advertise an Event input. Extending reusable
peripheral composition to Event ports belongs with the later pulse-reader/Event integration work.

## Roboport logistic-stock reader

`RoboportStockReaderDevice` exposes one roboport's logistic-network item contents as a persistent
Level-vector output. Its stable typed boundary is a single RED output named `stock`; because it is an
open vector, the device does not reserve one fixed scalar signal identity.

The physical device uses the real 4x4 roboport plus a one-tile constant-combinator output dock placed
flush against its east side. The roboport is configured to emit only on RED and to read logistic-network
contents. In Factorio's current roboport control enum, the serialized logistics mode is
`read_items_mode = 1` (`none / logistics / missing_requests` are 0 / 1 / 2).

Robot statistics are deliberately disabled in this device. Available/total logistic robots,
available/total construction robots, and roboport count are scalar metadata with distinct meanings;
putting them on the same unrestricted vector would mix status lanes into an item-stock bus. They can be
added later as separate typed scalar ports if an application needs them.

Generate the standalone reader with:

```bash
uv run python -m factorio_circuit.devices.roboport_stock_reader
```

Generate the compiler-integration probe with:

```bash
uv run python examples/roboport_stock_reader_probe.py
```

The integration probe realizes `oracle_signals("stock")` through a rigid provider component. The
compiler therefore sees the stock observation as an ordinary Level-vector oracle while preserving the
roboport's exact 4x4 geometry, RED connector contract, and raw Factorio control behavior through final
opaque-aware serialization.

## Belt and inserter Event pulse readers

`TransportBeltPulseReaderDevice` and `InserterPulseReaderDevice` are the Milestone F3 Event-input
peripherals. They turn Factorio's native one-tick item-read pulse into the compiler's existing physical
Event boundary rather than treating a transient pulse as a persistent Level.

The compiler represents one external vector Event physically as two synchronized nets: an open-vector
payload and a scalar `__valid` lane. The reusable reader therefore exposes two typed ports:

- RED `items`: Event vector payload;
- GREEN `valid`: Level scalar on fixed `signal-V`, used as the one-tick physical occurrence token.

A native belt/inserter pulse is duplicated onto RED and GREEN inside the reader. RED passes through an
arithmetic `each + 0` combinator; GREEN passes through a decider `anything > 0 => signal-V = 1`.
Both paths incur exactly one combinator tick, so payload and valid reach the compiler on the same
physical tick. This phase alignment is part of the device contract.

The Event frontend declarations are `Circuit.oracle_event(...)` for scalar payloads and
`Circuit.oracle_signal_event(...)` for vector payloads. A target provider receives an
`EventOraclePhysicalContext` and may bind a typed rigid device directly onto the already-lowered
`<name>` / `<name>__valid` abstract nets. The provider consumes those temporary external markers before
final placement; the opaque device endpoints are restored during the ordinary E2 composition path.

Generate the integrated F3 probe with:

```bash
uv run python examples/pulse_reader_event_probe.py
```

The probe accumulates the item Event vector into ordinary compiler state and can be built with either a
transport-belt or inserter reader. The resulting blueprint contains the real pulse sensor, the aligned
payload/valid conditioning pair, and the compiled Event accumulator in one routed artifact.

## Assembler device

`AssemblerDevice` is a reusable assembler plus logistic requester/provider I/O. It deliberately stops
below application policy: it does not decide what should be crafted, reserve inventory, or implement a
mall scheduler.

Its stable typed boundary is:

- green inputs: `recipe` (Level vector), `enable` (`signal-E` Level scalar), and
  `requester_demand` (Level vector);
- red outputs: `ingredients`, `requester_contents`, `provider_contents` (Level vectors), `working`
  (`signal-W` Level scalar), and `finished` (`signal-F` Event scalar).

The requester demand is a steady circuit-request setpoint. The device sanitizes the assembler's
current-recipe ingredient vector, exposes chest contents, reports working/finished state, and moves
completed products to its active-provider chest. Higher-level reservation/promise accounting belongs
outside the device.

Generate the standalone in-game probes with:

```bash
uv run python examples/assembler_device_probe.py
uv run python examples/assembler_device_anchor_probe.py
```

The second probe exercises the exact-overlap anchor ABI described in `device-anchoring.md`.

## Organization rule

Add future fixed peripherals as sibling generator modules under `factorio_circuit.devices`. Shared raw
blueprint encoding belongs in `devices/_blueprint.py`; compiler-owned logical/physical lowering stays
in the existing frontend/lowering/synthesis pipeline. Keep application policy out of reusable device
generators.
