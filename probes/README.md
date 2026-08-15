# Physical clock mechanics probes

These blueprints isolate the three Factorio mechanics questions that should be settled before
physical lowering of clocked/Event flows. They intentionally use raw Factorio 2.x blueprint JSON
rather than the compiler's Event lane, because Event-bearing modules still stop before physical
lowering.

Generate a blueprint string from the repository root with:

```bash
python -m probes.sum_into_boundary
python -m probes.vector_conditional_forward
python -m probes.belt_event_timing
```

For tick-sensitive observations, use a disposable test save and temporarily slow the game, for
example with `/c game.speed=0.1`.

## Probe 1: simultaneous `SumInto` boundary

`sum_into_boundary.py` is self-driving. A free-running counter creates a 240-tick repeating schedule:

1. source pulse `A=5`;
2. source pulse `A=7` and target pulse `T=1` on exactly the same tick;
3. source pulse `A=3`;
4. target pulse `T=1`.

The cell under test keeps its scalar memory on a red data network and reads the target pulse only from
the green network. On a target occurrence one decider snapshots the red input while the feedback
decider suppresses the next memory value.

Expected behavior:

- first snapshot pulse: `A=12`;
- second snapshot pulse: `A=3`;
- the convenience history signal `S` climbs `12, 15, 27, 30, ...`.

This confirms the right-closed interval needed by `SumInto`:

```text
(previous_target, current_target]
```

If the first snapshot is `5`, the simultaneous source occurrence is being excluded. If the second
snapshot includes stale pre-target state, the clear boundary is wrong.

## Probe 2: whole-vector conditional forwarding

`vector_conditional_forward.py` puts this payload on the red input network:

```text
iron-plate   = 11
copper-plate = 7
signal-A     = 5
```

The green input network contains only `signal-T=1`. The decider condition reads `T` from green only,
while an `Everything` output copies counts from red only.

Expected red output:

```text
iron-plate   = 11
copper-plate = 7
signal-A     = 5
```

`signal-T` should be absent. This determines whether one decider can implement a packed open-vector
gate/snapshot without leaking the control lane into the payload.

## Probe 3: external belt Event timing

`belt_event_timing.py` contains five eastbound transport belts. The middle belt reads contents in
**pulse** mode and emits only to its red circuit network. Its signal is also sent through `Each + 0 ->
Each` and exposed on a separate green network.

Drop an item onto the left end of the belt line and inspect the two probe networks:

- red pole: the raw belt pulse;
- green pole: the same payload after one arithmetic-combinator stage.

Expected behavior is a one-game-tick raw pulse followed exactly one game tick later by the delayed
copy. This establishes the first physical `ExternalEventClock` adapter convention.

## Recording results

For each probe, record the Factorio version and the observed signal sequence. These observations
should become target-mechanics regression notes before the physical Event lowerer relies on them.
