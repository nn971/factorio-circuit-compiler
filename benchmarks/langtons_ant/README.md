# Langton's Ant benchmark

This benchmark implements a finite toroidal 16x16 Langton's Ant using the existing eight-way player
movement detector and 16x16 packed-RGB lamp screen.

## Controls

The movement detector is used as a one-shot command pad:

```text
N   run continuously
S   pause
E   single-step
W   reset
```

Diagonal regions are neutral and therefore useful for re-arming commands. Remaining inside one
cardinal region does not repeatedly issue that command.

The ant starts paused at screen coordinate `(8, 8)`, facing north, on an all-white board. White turns
right; black turns left. The visited cell is flipped before movement. Board edges wrap toroidally so
the workload can run indefinitely on the fixed screen.

## Workload

The 256 board bits use the exact lamp-screen `PIXEL_SIGNALS` lanes as one packed `FreezeReg`. One step
performs:

```text
(x, y) -> scalar cell id
    -> 256-way one-hot decode
    -> dynamic membership test in packed board
    -> flip exactly that state lane
    -> data-dependent turn
    -> toroidal coordinate update
```

This makes Langton's Ant a focused benchmark for dynamic one-lane read/modify/write on a wide vector.
Snake mainly performs dense TTL/mask transformations; the two workloads therefore stress quite
different state-realization and temporal-mapping structures.

The framebuffer reuses the board lanes directly: black cells are rendered blue and the ant is red.
The trail under the current ant location is suppressed so the ant color remains unambiguous.

## Validation and generation

Run semantic/reference regressions:

```bash
uv run pytest tests/integration/test_langtons_ant.py
```

Generate an importable circuit blueprint:

```bash
uv run python -m benchmarks.langtons_ant.generate --output langtons-ant-blueprint.txt
```

Generate the two external devices separately:

```bash
uv run python -m factorio_circuit.devices.player_movement_detector
uv run python -m factorio_circuit.devices.lamp_screen
```

The generator prints the synthesized red/green wire colors required to connect `INPUT movement` and
`OUTPUT framebuffer`.
