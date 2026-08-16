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
the current in-game test and expose one shared `Level[Vector]`-style circuit bus with fixed virtual
signal lanes:

```text
signal-0 = N
signal-1 = NE
signal-2 = E
signal-3 = SE
signal-4 = S
signal-5 = SW
signal-6 = W
signal-7 = NW
```

The eight lamps from the tested prototype remain as direction indicators and are connected to the same
green circuit network. A consumer may connect to any of those indicator lamps to read the complete
one-hot vector.

## Organization rule

Add future fixed peripherals as sibling generator modules under `factorio_circuit.devices`. Shared raw
blueprint encoding belongs in `devices/_blueprint.py`; compiler-owned logical/physical lowering stays
in the existing frontend/lowering/synthesis pipeline.
