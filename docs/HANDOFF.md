# Fresh-Chat Handoff

## Goal

Compile a symbolic Python circuit EDSL to optimized Factorio 2.x combinator blueprints while keeping
logical stream semantics independent from physical combinator timing.

## Current milestone status

The Clocked Flow Semantics milestone is implemented on the merge-preparation line. Both ordinary
Level/state circuits and the implemented Event/clock-crossing subset reach Abstract Physical IR,
physical synthesis, Layout, and blueprint serialization.

The semantic reference Event simulator remains an independent oracle for irregular schedules and is
used by integration tests to check physical lowering.

The authoritative milestone closeout is:

- `docs/clocked-flow-milestone-closeout.md`

The original milestone design remains in:

- `docs/clocked-flow-semantics-milestone.md`

## Canonical semantic model

Canonical values are clocked flows with:

```text
payload shape
modality: Level or Event
structural clock identity
logical occurrence offset
```

Clock identity is separate from timing knowledge. `ClockContract` carries the known minimum
separation guarantee, while causality/timing analysis derives the physical requirement.

Logical causality and physical throughput are separate questions:

- same-clock zero-advance feedback is rejected semantically;
- legal feedback may require a slower inferred clock or a larger Event separation;
- external/fixed clocks are checked against the derived requirement.

## Logical time vocabulary

Inputs and registers use `.sample()` for Level observation. Expression `.step(n)` is pure flow-local
occurrence reindexing.

For an Event flow, positive `.step(n)` suppresses the first `n` occurrences and then preserves the
current payload on every surviving occurrence. Physical lowering implements this with a shared
occurrence counter and valid gating; it does not translate logical steps into game-tick delays.

`Circuit.step()` remains as compatibility syntax for the older Level/state frontend. `Circuit.tick()`
is reserved for future explicit physical scheduling. `register.value` remains a deprecated alias for
`register.sample()`.

## Level/periodic route

```text
symbolic circuit
    ↓
canonical semantic IR
    ↓
logical causality + state timing
    ↓
AbstractPhysicalCircuit
    ↓
physical synthesis + Layout
    ↓
blueprint
```

Periodic state may infer a multicycle physical period. Feed-forward latency does not itself enlarge
the period; recurrence constraints do. Independent state domains may infer different periods where
supported by the existing Level path.

`AccumulatorReg` and `FreezeReg` remain the foundational whole-vector state primitives.

## Event/clocked route

External Events use a payload-plus-valid ABI:

```text
name
name__valid
```

The implemented semantic/physical vocabulary includes:

- scalar/vector external Events;
- `sample_on(level, event_clock)`;
- `gate_clock(parent, when=...)`;
- additive `event_merge(...)`;
- stateful vector `hold_into(source, target)`;
- stateful vector `sum_into(source, target)`;
- Event `.step(n)` occurrence-tail semantics;
- explicit HOLD/ZERO/VALID boundary materialization;
- Event Freeze updates;
- direct unconditional Event accumulation;
- physical synthesis and blueprint generation for the supported subset.

Important simultaneous-boundary rules:

```text
HoldInto: strict-prior source value
SumInto:  (previous target, current target]
```

Thus a source occurrence simultaneous with a target is excluded from the current `HoldInto` sample
but included in the current `SumInto` interval.

## Output materialization

Sparse Event outputs explicitly select:

```text
HOLD
ZERO
VALID
```

VALID exposes aligned payload and `<output>__valid` ports. Zero payload and absent occurrence remain
semantically distinct.

## Flagship acceptance test

`tests/integration/test_multi_rate_event_ledger.py` combines:

- three irregular vector Event producers;
- simultaneous producer occurrences;
- shared `EventMerge`;
- a gated reporting clock;
- three `SumInto` bridges;
- direct lifetime Event accumulation;
- VALID Event outputs and held Level state.

It compares the compiled physical circuit timestamp-by-timestamp against `simulate_events(...)` and
also checks that shared EventMerge/bridge work is not duplicated per downstream use.

## In-game pre-merge smoke

Generate:

```fish
uv run python examples/clocked_flow_ingame_smoke.py
```

Then follow `docs/clocked-flow-merge-smoke.md`. The test intentionally combines Event `.step(1)`, a
gated target clock, `HoldInto`, `SumInto`, and VALID materialization so one in-game circuit checks the
most timing-sensitive boundaries before merging.

## Current validation baseline

The final implementation head before merge preparation passed:

```text
pytest: 310 passed
ruff check: passed
ruff format --check: passed
mypy src: passed
```

Run the same full checks on the exact merge candidate; do not infer green status from this handoff.

## Deliberately deferred work

These are follow-up milestones rather than Clocked Flow merge blockers:

- general Event queues/FIFOs and overload buffering;
- ready/valid backpressure protocols for external devices;
- richer burst/rate clock contracts beyond minimum separation;
- arbitrary combinations of Event add/clear/replace updates;
- a general Event/state fusion optimizer;
- clock-aware packing across every Event expression family;
- retirement of all compatibility sampled wrappers and `Circuit.step()`;
- stable-vs-experimental Factorio capability profiles.

## Autonomous market

The temporal substrate needed to replace short-pulse polling workarounds now exists. Migrating the
autonomous-market controller to Event-oriented device protocols is a separate application milestone,
not unfinished Clocked Flow semantics.
