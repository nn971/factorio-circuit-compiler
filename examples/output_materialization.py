"""Choose dense boundary behavior without changing sparse internal Event semantics."""

from factorio_circuit import (
    Circuit,
    EventOccurrence,
    EventSchedule,
    OutputMaterializationPolicy,
    materialize_output_trace,
    simulate_events,
)

circuit = Circuit("output_materialization")
finished = circuit.event("finished", guaranteed_min_separation=1)
left = circuit.event("left", guaranteed_min_separation=1)
right = circuit.event("right", guaranteed_min_separation=1)

# A general Event defaults to VALID because payload 0 may still be a real occurrence.
circuit.output("finished_code", finished)

# EventMerge is explicitly additive, so absence naturally materializes as zero.
total = circuit.event_merge(left, right)
circuit.output("total", total)

# Policies are boundary choices and can be overridden without changing the internal Event flow.
circuit.output("last_finished", finished, policy=OutputMaterializationPolicy.HOLD)
module = circuit.build()

result = simulate_events(
    module,
    (),
    (
        EventSchedule(finished, (EventOccurrence(1, 0), EventOccurrence(4, 7))),
        EventSchedule(left, (EventOccurrence(2, 3),)),
        EventSchedule(right, (EventOccurrence(5, 4),)),
    ),
    stop_timestamp=7,
)

valid = materialize_output_trace(result, module, "finished_code")
zero = materialize_output_trace(result, module, "total")
hold = materialize_output_trace(result, module, "last_finished")

assert valid.payloads == (0, 0, 0, 0, 7, 0, 0)
assert valid.valid == (False, True, False, False, True, False, False)
assert valid.valid_name == "finished_code__valid"
assert zero.payloads == (0, 0, 3, 0, 0, 4, 0)
assert hold.payloads == (0, 0, 0, 0, 7, 7, 7)
