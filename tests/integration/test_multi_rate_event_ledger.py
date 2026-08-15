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


def test_multi_rate_event_ledger_matches_reference_and_shares_merge() -> None:
    circuit = Circuit("multi_rate_event_ledger")
    enabled = circuit.input("enabled")
    worker0 = circuit.signal_event("worker0", guaranteed_min_separation=5)
    worker1 = circuit.signal_event("worker1", guaranteed_min_separation=4)
    worker2 = circuit.signal_event("worker2", guaranteed_min_separation=5)
    fast_tick = circuit.event("fast_tick", guaranteed_min_separation=4)
    slow_tick = circuit.event("slow_tick", guaranteed_min_separation=5)
    audit_tick = circuit.event("audit_tick", guaranteed_min_separation=1)

    merged = circuit.event_merge(worker0, worker1, worker2)
    slow_report = circuit.gate_clock(
        slow_tick,
        when=circuit.sample_on(enabled, slow_tick),
    )
    fast_sum = circuit.sum_into(merged, fast_tick)
    slow_sum = circuit.sum_into(merged, slow_report)
    audit_sum = circuit.sum_into(merged, audit_tick)
    lifetime = circuit.accumulator("lifetime")
    lifetime.add(merged.step(0))

    circuit.output("fast", fast_sum, policy=OutputMaterializationPolicy.VALID)
    circuit.output("slow", slow_sum, policy=OutputMaterializationPolicy.VALID)
    circuit.output("audit", audit_sum, policy=OutputMaterializationPolicy.VALID)
    circuit.output("lifetime", lifetime.sample())

    worker0_rows = {
        0: {IRON: 2},
        5: {IRON: 3},
        10: {IRON: 5},
    }
    worker1_rows = {
        1: {COPPER: 4},
        5: {IRON: 1},
        9: {COPPER: 6},
    }
    worker2_rows = {
        3: {IRON: 7},
        8: {COPPER: 2},
    }
    fast_rows = {2: 1, 6: 1, 10: 1, 14: 1}
    slow_rows = {4: 1, 9: 1, 14: 1}
    audit_rows = {14: 1}

    level_rows = [{"enabled": int(tick in {4, 14})} for tick in range(15)]
    reference = simulate_events(
        circuit.build(),
        level_rows,
        [
            _schedule(worker0, worker0_rows),
            _schedule(worker1, worker1_rows),
            _schedule(worker2, worker2_rows),
            _schedule(fast_tick, fast_rows),
            _schedule(slow_tick, slow_rows),
            _schedule(audit_tick, audit_rows),
        ],
        stop_timestamp=15,
    )
    reference_traces = {
        "fast": materialize_event_trace(
            reference,
            fast_sum,
            EventMaterializationPolicy.VALID,
        ),
        "slow": materialize_event_trace(
            reference,
            slow_sum,
            EventMaterializationPolicy.VALID,
        ),
        "audit": materialize_event_trace(
            reference,
            audit_sum,
            EventMaterializationPolicy.VALID,
        ),
    }

    assert reference_traces["fast"].payloads[2] == {IRON: 2, COPPER: 4}
    assert reference_traces["fast"].payloads[6] == {IRON: 11}
    assert reference_traces["fast"].payloads[10] == {IRON: 5, COPPER: 8}
    assert reference_traces["fast"].payloads[14] == {}
    assert reference_traces["slow"].payloads[4] == {IRON: 9, COPPER: 4}
    # slow_tick@9 is gated away, so the second slow interval remains open through t=14.
    assert reference_traces["slow"].payloads[14] == {IRON: 9, COPPER: 8}
    assert reference_traces["audit"].payloads[14] == {IRON: 18, COPPER: 12}
    assert reference.final_state["lifetime"] == {IRON: 18, COPPER: 12}

    compiled = compile_circuit(circuit)
    output_ports = {
        port.name: (index, port.phase)
        for index, port in enumerate(compiled.abstract_physical.outputs)
    }
    max_phase = max(port.phase for port in compiled.abstract_physical.outputs)

    physical_rows: list[dict[str, object]] = []
    for tick in range(15):
        row: dict[str, object] = {
            "enabled": level_rows[tick]["enabled"],
            "worker0": worker0_rows.get(tick, {}),
            "worker0__valid": int(tick in worker0_rows),
            "worker1": worker1_rows.get(tick, {}),
            "worker1__valid": int(tick in worker1_rows),
            "worker2": worker2_rows.get(tick, {}),
            "worker2__valid": int(tick in worker2_rows),
            "fast_tick": fast_rows.get(tick, 0),
            "fast_tick__valid": int(tick in fast_rows),
            "slow_tick": slow_rows.get(tick, 0),
            "slow_tick__valid": int(tick in slow_rows),
            "audit_tick": audit_rows.get(tick, 0),
            "audit_tick__valid": int(tick in audit_rows),
        }
        physical_rows.append(row)

    physical = simulate_stream(
        compiled.physical_circuit,
        physical_rows,
        flush_ticks=max_phase + 3,
    )

    for name, semantic in reference_traces.items():
        payload_index, payload_phase = output_ports[name]
        valid_index, valid_phase = output_ports[f"{name}__valid"]
        assert payload_phase == valid_phase
        for timestamp in range(15):
            physical_row = physical[timestamp + payload_phase]
            assert physical_row[payload_index] == semantic.payloads[timestamp]
            assert physical_row[valid_index] == int(semantic.valid[timestamp])  # type: ignore[index]

    lifetime_index, _lifetime_phase = output_ports["lifetime"]
    assert physical[-1][lifetime_index] == {IRON: 18, COPPER: 12}

    # Three reporting bridges and the lifetime state reuse one physical realization of the merged
    # producer payload. The N-way vector add appears once (N-1 stages), not once per downstream use.
    descriptions = [
        getattr(entity, "description", "") for entity in compiled.abstract_physical.entities
    ]
    merge_prefix = f"EventMerge {merged.name}: add parent["
    assert sum(description.startswith(merge_prefix) for description in descriptions) == 2
    assert (
        sum("Event Accumulator lifetime: add gated occurrence" in item for item in descriptions)
        == 1
    )
