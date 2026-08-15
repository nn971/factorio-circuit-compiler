"""Aggregate vector Events into explicit report-clock intervals."""

from factorio_circuit import (
    Circuit,
    EventMaterializationPolicy,
    EventOccurrence,
    EventSchedule,
    SignalId,
    materialize_event_trace,
    simulate_events,
)

ITEM = SignalId("item", "iron-plate")

circuit = Circuit("sum_into")
worker_a = circuit.signal_event("worker_a", guaranteed_min_separation=2)
worker_b = circuit.signal_event("worker_b", guaranteed_min_separation=2)
report = circuit.event("report", guaranteed_min_separation=5)

# Merge before crossing: all downstream users of this conversion share one packed vector bridge.
produced = circuit.event_merge(worker_a, worker_b)
assert hasattr(produced, "_as_signals")
reported = circuit.sum_into(produced, report)  # type: ignore[arg-type]

result = simulate_events(
    circuit.build(),
    (),
    (
        EventSchedule(
            worker_a,
            (
                EventOccurrence(1, {ITEM: 2}),
                EventOccurrence(5, {ITEM: 3}),
            ),
        ),
        EventSchedule(worker_b, (EventOccurrence(3, {ITEM: 4}),)),
        EventSchedule(report, (EventOccurrence(5, 1),)),
    ),
    stop_timestamp=7,
)
trace = materialize_event_trace(result, reported, EventMaterializationPolicy.VALID)

# SumInto uses (previous_report, current_report], so worker_a's t=5 payload is included.
assert trace.payloads[5] == {ITEM: 9}
assert trace.valid is not None and trace.valid[5]
