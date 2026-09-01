# 2048 benchmark

This benchmark implements a deterministic, interactive 4x4 2048 game for the existing eight-way
player movement detector and 16x16 packed-RGB lamp screen.

## Controls

The movement detector is treated as a one-shot command pad:

```text
N / E / S / W   move the board
NW              reset to the initial two-tile board
NE / SE / SW    unused; useful as neutral/re-arm regions
```

Entering a command region from neutral produces one command. Remaining in the same region does not
auto-repeat. Return through the neutral center or an unused diagonal region before issuing the next
command.

## Workload

The board is one packed 16-lane `FreezeReg`. Each successful command evaluates the ordinary 2048
pipeline

```text
stable zero compaction
    -> disjoint equal-pair merge
    -> stable zero compaction
    -> score accumulation
    -> deterministic spawn
```

for the selected direction. The circuit computes all four directional candidates from the same old
state and selects one with the detector lanes. Tile counts are ordinary powers of two, so the merged
tile value is also the standard score increment.

For reproducible semantic and physical acceptance, a successful move spawns into the first empty
row-major cell. Every tenth successful move spawns a `4`; other successful moves spawn a `2`. This is
an intentional deterministic benchmark source, analogous to deterministic-food Snake. A future
random-oracle variant can replace the spawn policy without changing the merge/compaction kernel.

Each logical board cell occupies a 4x4 display region with a 3x3 colored tile and a one-pixel dark
grid separator. The framebuffer therefore exercises sixteen scalar-to-vector block expansions and a
256-lane packed-RGB output.

## Validation

Cheap reference and construction regressions:

```bash
uv run pytest tests/integration/test_game_2048.py
```

The full symbolic gameplay comparisons are marked `slow` and `acceptance`; run them explicitly when
changing the move/state logic:

```bash
uv run pytest -m 'slow and acceptance' tests/integration/test_game_2048.py
```

Generate an importable circuit blueprint:

```bash
uv run python -m benchmarks.game_2048.generate --output game-2048-blueprint.txt
```

Generate the two reusable external devices separately:

```bash
uv run python -m factorio_circuit.devices.player_movement_detector
uv run python -m factorio_circuit.devices.lamp_screen
```

The generator prints the synthesized red/green wire colors required to connect `INPUT movement` and
`OUTPUT framebuffer`.
