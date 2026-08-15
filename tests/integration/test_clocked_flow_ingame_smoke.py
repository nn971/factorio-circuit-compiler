from factorio_circuit import Circuit, SignalId, compile_circuit
from factorio_circuit.ir.output import OutputMaterializationPolicy
from factorio_circuit.simulate.clocked_events import simulate_events
from factorio_circuit.simulate.events import (
    EventMaterializationPolicy,
    EventOccurrence,
    EventSchedule,
    materialize_event_trace,
)
from factorio_circuit.simulate.physical import simulate_stream

IRON = SignalId("item", "iron-plate")
COPPER = SignalId("item", "copper-plate")


def _schedule(source: object, rows: dict[int, object]) -> EventSchedule:
    return EventSchedule(
        source,  # type: ignore[arg-type]
        tuple(EventOccurrence(timestamp, payload) for timestamp, payload in sorted(rows.items())),
    )


def test_clocked_flow_ingame_smoke_matches_reference() -> None:
    circuit = Circuit("clocked_flow_ingame_smoke")
    enabled = circuit.input("enabled")
    source = circuit.signal_event("source", guaranteed_min_separation=4)
    tick = circuit.event("tick", guaranteed_min_separation=5)

    gated_tick = circuit.gate_clock(
        tick,
        when=circuit.sample_on(enabled, tick),
    )
    tail = source.step(1)
    held = circuit.hold_into(source, gated_tick)
    window = circuit.sum_into(source, gated_tick)

    circuit.output("tail", tail, policy=OutputMaterializationPolicy.VALID)
    circuit.output("held", held, policy=OutputMaterializationPolicy.VALID)
    circuit.output("window", window, policy=OutputMaterializationPolicy.VALID)

    source_rows = {
        0: {IRON: 5},
        4: {COPPER: 7},
        8: {IRON: 3},
    }
    tick_rows = {4: 1, 9: 1, 14: 1}
    level_rows = [{"enabled": int(index in {4, 14})} for index in range(15)]

    reference = simulate_events(
        circuit.build(),
        level_rows,
        [
            _schedule(source, source_rows),
            _schedule(tick, tick_rows),
        ],
        stop_timestamp=15,
    )
    expected = {
        "held": materialize_event_trace(
            reference,
            held,
            EventMaterializationPolicy.VALID,
        ),
        "window": materialize_event_trace(
            reference,
            window,
            EventMaterializationPolicy.VALID,
        ),
    }

    assert expected["held"].payloads[4] == {IRON: 5}
    assert expected["held"].valid[9] is False
    assert expected["held"].payloads[14] == {IRON: 3}
    assert expected["window"].payloads[4] == {IRON: 5, COPPER: 7}
    assert expected["window"].valid[9] is False
    assert expected["window"].payloads[14] == {IRON: 3}

    compiled = compile_circuit(circuit)
    ports = {
        port.name: (index, port.phase)
        for index, port in enumerate(compiled.abstract_physical.outputs)
    }
    max_phase = max(port.phase for port in compiled.abstract_physical.outputs)

    physical_rows: list[dict[str, object]] = []
    for index in range(15):
        physical_rows.append(
            {
                "enabled": level_rows[index]["enabled"],
                "source": source_rows.get(index, {}),
                "source__valid": int(index in source_rows),
                "tick": tick_rows.get(index, 0),
                "tick__valid": int(index in tick_rows),
            }
        )

    physical = simulate_stream(
        compiled.physical_circuit,
        physical_rows,
        flush_ticks=max_phase + 3,
    )

    # A reindexed Event expression is not itself a declared Event/SampleOn reference accepted by
    # materialize_event_trace().  Validate its physical tail directly, matching the dedicated
    # occurrence-reindex regression: only the first occurrence is suppressed.
    tail_index, tail_phase = ports["tail"]
    tail_valid_index, tail_valid_phase = ports["tail__valid"]
    assert tail_phase == tail_valid_phase
    assert physical[0 + tail_phase][tail_valid_index] == 0
    assert physical[4 + tail_phase][tail_index] == {COPPER: 7}
    assert physical[4 + tail_phase][tail_valid_index] == 1
    assert physical[8 + tail_phase][tail_index] == {IRON: 3}
    assert physical[8 + tail_phase][tail_valid_index] == 1
    assert sum(row[tail_valid_index] for row in physical) == 2

    for name, semantic in expected.items():
        payload_index, payload_phase = ports[name]
        valid_index, valid_phase = ports[f"{name}__valid"]
        assert payload_phase == valid_phase
        for timestamp in range(15):
            row = physical[timestamp + payload_phase]
            assert row[payload_index] == semantic.payloads[timestamp]
            assert row[valid_index] == int(semantic.valid[timestamp])  # type: ignore[index]
