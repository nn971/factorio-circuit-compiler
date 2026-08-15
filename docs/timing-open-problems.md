# Timing open problems

The original pulse-loss/Event-sampling problem that motivated the clocked-flow milestone is now
resolved at the semantic and physical-lowering level. External Events have explicit clocks and valid
signals, Event state has clock contracts, `SampleOn` snapshots Levels on Event clocks, `SumInto` and
`HoldInto` preserve cross-clock information with explicit policies, and physical simulation is checked
against the irregular semantic Event runner.

This document now records the timing problems that remain after that milestone.

## Overload requires explicit buffering or backpressure

An Event recurrence can be logically causal but physically too slow for the environment. The compiler
derives `required_min_separation` and compares it with the clock's `guaranteed_min_separation`.
Insufficient spacing raises `EventThroughputError`; occurrences are never silently dropped.

What remains open is how a program should intentionally accept a faster producer while executing a
slower consumer. The semantic choices are necessarily explicit:

- prove a stronger producer spacing contract;
- aggregate into another execution clock, for example with `SumInto`;
- add an explicit pending latch/FIFO/queue;
- expose ready/valid backpressure to the environment.

A hidden one-event buffer should not be introduced automatically into every slow domain.

## General Event queues and ready/valid protocols

The current physical Event path preserves the milestone bridge vocabulary but does not provide a
general queue. A future queue protocol needs to define at least:

```text
arrival clock
execution/dequeue clock
capacity
full/empty behavior
simultaneous enqueue/dequeue boundary
backpressure or overflow policy
```

This is particularly relevant for external-device drivers where the environment may produce events
independently of the controller's recurrence latency.

## Richer arrival contracts

`guaranteed_min_separation` is enough for direct recurrence feasibility but cannot describe arbitrary
bursts. A future contract could express bounds such as

```text
at most N activations in every W game ticks
```

or equivalent token-bucket constraints. Such contracts would become useful once bounded queues are
part of physical lowering.

## General Event-state programs

Physical Event state currently covers Event-triggered Freeze operations, compiler-owned bridge state,
and one unconditional Event `AccumulatorReg.add(...)` transition per ordinary accumulator. More
general state programs remain open, including:

- multiple independent Event transitions targeting one register;
- Event accumulator clear/replace combinations;
- arbitrary Event conditions mixed with additive updates;
- broader automatic bridge/state fusion.

These need explicit same-timestamp transition ordering and physical phase rules rather than ad-hoc
special cases.

## Cross-clock state beyond the bridge vocabulary

`SampleOn`, `SumInto`, and `HoldInto` cover the common sampling, additive-history, and latest-value
crossings. Other policies may eventually be useful—for example min/max reduction over an interval,
first/last occurrence capture, bounded lists, or priority arbitration. They should be introduced as
explicit semantic crossings whenever they preserve information differently.

## External device latency contracts

The autonomous-market recipe reader demonstrated that an external machine can require a settling
interval that is not represented by internal combinator latency. Clocked flows solve event presence
and re-clocking, but device drivers still need a way to describe physical I/O latency and protocol
requirements such as:

```text
request assertion -> response valid after at least L ticks
request must remain held until acknowledgement
output may remain stale for D ticks after completion
```

These belong naturally to the planned external-device protocol/driver layer rather than to ordinary
combinator timing.

## Short-lived Levels

An Event is reliable because presence is explicit. A genuinely Level-like source is still sampled by
value at a chosen clock activation. If an external condition rises and falls entirely between those
activations, then by definition it was not observed as a Level.

If that transient must be preserved, the device interface should expose it as an Event or explicitly
latch/aggregate it. The compiler should not guess that every changing Level is secretly an Event.

The existing autonomous-market `Read working` protocol can continue to rely on a sufficiently long
Level interval, but an Event-oriented completion protocol is now possible and is the preferred future
migration when the game/device interface can supply one.
