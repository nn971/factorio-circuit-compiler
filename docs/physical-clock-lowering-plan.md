# Physical clock lowering implementation plan

This branch starts physical realization of the clocked/Event semantic lane after the three mechanics probes were confirmed in Factorio.

Confirmed target conventions:

- an external Event is represented physically by a payload bus plus a one-tick valid/activation pulse;
- same-clock feed-forward payload logic may evaluate continuously while the valid pulse is delayed to the result phase;
- `SampleOn(level, event_clock)` reuses the continuously available Level payload and adopts the Event activation pulse;
- decider copied-count network selection can forward a packed open vector from one wire color without leaking a control lane from the other color;
- `SumInto` may use a right-closed `(previous_target, current_target]` snapshot/reset boundary, including a source occurrence simultaneous with the target;
- belt pulse mode is a suitable first physical external-Event adapter and a combinator stage delays its payload by one game tick.

## Initial implementation slice

1. Add a clock-aware physical lowering route alongside the existing Level-only lowerer.
2. Introduce target records for a realized activation token and a realized clocked scalar/vector.
3. Physically expose external Event payload and valid ports.
4. Lower `SampleOn` and same-clock feed-forward Event expressions by sharing/delaying one activation token.
5. Materialize Event outputs with `VALID` and `ZERO`; add `HOLD` using an explicit target register once the feed-forward route is stable.
6. Consume `analyze_clocked_timing()` and `validate_event_throughput()` before physical construction.
7. Add physical/reference integration tests for irregular schedules and phase alignment.
8. Extend the route to event-clocked state, then `SumInto`, using the confirmed snapshot/reset topology.

The old Level compiler route remains unchanged while this lane is brought up incrementally.
