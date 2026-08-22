# Clock-aware in-game examples

These examples are intentionally small enough to read as a semantic ladder. Heavy whole-compiler
workloads live under `benchmarks/`; in particular the playable Snake stress test is documented in
`benchmarks/snake/README.md`.

Each executable case prints two Factorio blueprint strings:

1. a small **driver** that repeats a deterministic input/Event schedule;
2. the **compiled circuit** being tested.

Paste both blueprints, power the driver, then connect every labeled `DRIVER OUTPUT <name>` terminal to
the matching compiled `INPUT <name>` marker. The script prints the required red/green wire color.
No hand-timed one-tick `__valid` pulses are needed.

The driver loops forever. For persistent accumulators, the documented value is the first-cycle value;
later cycles continue to add to it.

## 1. Event presence

```bash
uv run python -m examples.event_basics pulse_echo
```

Checks the physical Event ABI and `VALID` materialization. One scheduled occurrence deliberately has
payload zero: `echo__valid` must still pulse.

## 2. Level sampled on an Event clock

```bash
uv run python -m examples.event_basics sample_on
```

Checks `SampleOn(Level, Event)`. Only trigger occurrences produce output samples.

## 3. Logical occurrence reindexing

```bash
uv run python -m examples.event_basics occurrence_step
```

Checks `source.step(1)`. The source events have unequal physical spacing; the first occurrence is
suppressed and the later occurrences survive unchanged. `.step(1)` counts occurrences, not game ticks.

## 4. Derived subclock

```bash
uv run python -m examples.derived_clocks gate_clock
```

Checks `GateClock`. Four parent ticks occur, but the sampled Level predicate enables only two of them.

## 5. Additive Event union

```bash
uv run python -m examples.derived_clocks event_merge
```

Checks `EventMerge`. Independent parent occurrences pass through; two simultaneous producers at phase
80 coalesce into one occurrence and their iron counts add.

## 6. Strict-prior latest-value crossing

```bash
uv run python -m examples.clock_crossings hold_into
```

Checks `HoldInto`. A source update and report occur simultaneously at phase 80. The report must see
the previously held value; the simultaneous source becomes visible only to the next report.

## 7. Right-closed additive crossing

```bash
uv run python -m examples.clock_crossings sum_into
```

Checks `SumInto`. A source update simultaneous with the phase-100 report belongs to the current
`(previous_report, current_report]` window.

The `hold_into` and `sum_into` examples intentionally put their opposite simultaneous-boundary rules
next to each other.

## 8. Event-driven persistent state

```bash
uv run python -m examples.event_state
```

An irregular vector Event feeds an `AccumulatorReg`. The sparse source is exposed with `VALID`, while
`lifetime` is a persistent Level output. This is the smallest stateful Event example.

## 9. Multi-rate production ledger

```bash
uv run python -m examples.multi_rate_ledger
```

The capstone combines three irregular producers, `EventMerge`, a gated reporting clock, three
`SumInto` bridges at different report rates, simultaneous producer occurrences, and a lifetime Event
accumulator. It is a human-readable version of the main multi-rate Event integration stress case.

## What to inspect in game

For `VALID` outputs, observe the payload marker and its `<name>__valid` companion together. They should
change at the same physical phase. The exact absolute game tick depends on when the driver was powered;
the documented **driver phase** and ordering repeat every cycle and are what matter.

If a case fails, prefer debugging the earliest failing example in this list. Every later example
assumes the earlier contracts.

## Research scaffold: autonomous mall

`examples/autonomous_mall/` currently contains only the retained offline research core: real Factorio
recipe extraction, canonical recipe-DAG construction, exact quality/recycling mechanics, and an exact
expected-flow material-efficiency oracle. The earlier manual worker rows, runtime controllers, ROMs,
and scanners were exploratory and have been removed.

There is currently no accepted circuit-side autonomous-mall architecture. See
`examples/autonomous_mall/README.md` for the retained assumptions and validation commands.

Generic device work developed during those experiments remains reusable and has separate examples:

```bash
uv run python -m examples.assembler_device_probe
uv run python -m examples.assembler_device_anchor_probe
```
