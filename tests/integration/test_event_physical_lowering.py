from dataclasses import replace
from typing import cast

from factorio_circuit import Circuit, SignalId, compile_circuit
from factorio_circuit.frontend import Expr, SignalsExpr
from factorio_circuit.ir.output import (
    MaterializedReturnValue,
    OutputMaterialization,
    OutputMaterializationPolicy,
)
from factorio_circuit.simulate.physical import simulate_stream

IRON = SignalId("item", "iron-plate")
COPPER = SignalId("item", "copper-plate")


def _with_hold_output(circuit: Circuit) -> object:
    module = circuit.build()
    return replace(
        module,
        output=MaterializedReturnValue(
            module.output.values,
            module.output.names,
            (OutputMaterialization(OutputMaterializationPolicy.HOLD),),
        ),
    )


def test_scalar_event_hold_ignores_invalid_payload_and_valid_zero_clears() -> None:
    circuit = Circuit("scalar_event_hold")
    event = circuit.event("event", guaranteed_min_separation=1)
    value = cast(Expr, event + 0)
    circuit.output("held", value)

    compiled = compile_circuit(_with_hold_output(circuit))  # type: ignore[arg-type]
    assert [port.name for port in compiled.abstract_physical.outputs] == ["held"]
    assert compiled.abstract_physical.outputs[0].phase == 2

    trace = simulate_stream(
        compiled.physical_circuit,
        [
            {"event": 5, "event__valid": 1},
            {"event": 99, "event__valid": 0},
            {"event": 0, "event__valid": 1},
            {"event": 42, "event__valid": 0},
        ],
        flush_ticks=2,
    )

    assert trace == [(0,), (0,), (5,), (5,), (0,), (0,)]


def test_vector_event_hold_preserves_last_valid_vector_and_empty_event_clears() -> None:
    circuit = Circuit("vector_event_hold")
    event = circuit.signal_event("event", guaranteed_min_separation=1)
    value = cast(SignalsExpr, event + circuit.constant_signals({}))
    circuit.output("held", value)

    compiled = compile_circuit(_with_hold_output(circuit))  # type: ignore[arg-type]
    assert [port.name for port in compiled.abstract_physical.outputs] == ["held"]
    assert compiled.abstract_physical.outputs[0].phase == 2

    trace = simulate_stream(
        compiled.physical_circuit,
        [
            {"event": {IRON: 2}, "event__valid": 1},
            {"event": {COPPER: 99}, "event__valid": 0},
            {"event": {COPPER: 4}, "event__valid": 1},
            {"event": {IRON: 77}, "event__valid": 0},
            {"event": {}, "event__valid": 1},
        ],
        flush_ticks=2,
    )

    assert trace == [
        ({},),
        ({},),
        ({IRON: 2},),
        ({IRON: 2},),
        ({COPPER: 4},),
        ({COPPER: 4},),
        ({},),
    ]


def test_vector_event_capture_commits_payload_and_empty_occurrence_clears() -> None:
    circuit = Circuit("vector_event_capture")
    event = circuit.signal_event("event", guaranteed_min_separation=2)
    memory = circuit.freeze("memory")
    memory.capture_on(event, required_min_separation=1)
    circuit.output("memory", memory.sample())

    compiled = compile_circuit(circuit)
    assert [port.name for port in compiled.abstract_physical.outputs] == ["memory"]
    assert compiled.abstract_physical.outputs[0].phase == 0

    trace = simulate_stream(
        compiled.physical_circuit,
        [
            {"event": {IRON: 2}, "event__valid": 1},
            {"event": {COPPER: 99}, "event__valid": 0},
            {"event": {}, "event__valid": 1},
            {"event": {IRON: 77}, "event__valid": 0},
        ],
        flush_ticks=2,
    )

    assert trace == [
        ({},),
        ({IRON: 2},),
        ({IRON: 2},),
        ({},),
        ({},),
        ({},),
    ]


def test_event_capture_coupling_uses_old_state_snapshot() -> None:
    circuit = Circuit("event_capture_old_state")
    data = cast(SignalsExpr, circuit.signals("data"))
    event = circuit.signal_event("event", guaranteed_min_separation=3)
    source = circuit.freeze("source")
    target = circuit.freeze("target")
    old_source = source.sample()
    source.capture_on(event, required_min_separation=1)
    target.capture_on(event, (old_source + data).positive(), required_min_separation=1)
    circuit.output("source", source.sample())
    circuit.output("target", target.sample())

    compiled = compile_circuit(circuit)
    trace = simulate_stream(
        compiled.physical_circuit,
        [
            {"data": {IRON: 4}, "event": {IRON: 2}, "event__valid": 1},
            {"data": {IRON: 100}, "event": {}, "event__valid": 0},
            {"data": {IRON: 100}, "event": {}, "event__valid": 0},
            {"data": {IRON: 100}, "event": {}, "event__valid": 0},
        ],
        flush_ticks=2,
    )

    # Source captures the Event payload after one tick.  Target's expression is evaluated from the
    # old source snapshot and the Level row present at the occurrence, then commits after its own
    # combinational latency.  It must therefore become 4, not 6 or 100/102.
    assert trace[0] == ({}, {})
    assert trace[1][0] == {IRON: 2}
    assert trace[-1] == ({IRON: 2}, {IRON: 4})


def test_sum_into_physical_bridge_is_right_closed_and_resets_between_targets() -> None:
    circuit = Circuit("sum_into_physical")
    source = circuit.signal_event("source", guaranteed_min_separation=2)
    target = circuit.event("target", guaranteed_min_separation=3)
    summed = circuit.sum_into(source, target)
    circuit.output(
        "sum",
        summed,
        policy=OutputMaterializationPolicy.VALID,
    )

    compiled = compile_circuit(circuit)
    assert [port.name for port in compiled.abstract_physical.inputs] == [
        "source",
        "source__valid",
        "target",
        "target__valid",
    ]
    assert [port.name for port in compiled.abstract_physical.outputs] == [
        "sum",
        "sum__valid",
    ]
    assert compiled.abstract_physical.outputs[0].phase == 3
    assert compiled.abstract_physical.outputs[1].phase == 3

    trace = simulate_stream(
        compiled.physical_circuit,
        [
            {"source": {IRON: 2}, "source__valid": 1, "target": 0, "target__valid": 0},
            {
                "source": {COPPER: 99},
                "source__valid": 0,
                "target": 0,
                "target__valid": 0,
            },
            {"source": {IRON: 3}, "source__valid": 1, "target": 1, "target__valid": 1},
            {
                "source": {IRON: 77},
                "source__valid": 0,
                "target": 0,
                "target__valid": 0,
            },
            {"source": {COPPER: 4}, "source__valid": 1, "target": 0, "target__valid": 0},
            {"source": {}, "source__valid": 0, "target": 1, "target__valid": 1},
        ],
        flush_ticks=3,
    )

    # Target t=2 includes the simultaneous source contribution: 2 + 3. The t=1 speculative
    # payload is invalid and must not enter the accumulator. After the snapshot the feedback path
    # is suppressed for one tick, so the t=5 target observes only the new t=4 contribution.
    assert trace[5] == ({IRON: 5}, 1)
    assert trace[8] == ({COPPER: 4}, 1)
    assert all(valid == 0 for index, (_, valid) in enumerate(trace) if index not in {5, 8})


def test_hold_into_physical_bridge_samples_strictly_prior_source_value() -> None:
    circuit = Circuit("hold_into_physical")
    source = circuit.signal_event("source", guaranteed_min_separation=3)
    target = circuit.event("target", guaranteed_min_separation=1)
    held = circuit.hold_into(source, target)
    circuit.output("held", held, policy=OutputMaterializationPolicy.VALID)

    compiled = compile_circuit(circuit)
    assert [port.name for port in compiled.abstract_physical.inputs] == [
        "source",
        "source__valid",
        "target",
        "target__valid",
    ]
    assert [port.name for port in compiled.abstract_physical.outputs] == [
        "held",
        "held__valid",
    ]
    assert compiled.abstract_physical.outputs[0].phase == 1
    assert compiled.abstract_physical.outputs[1].phase == 1

    trace = simulate_stream(
        compiled.physical_circuit,
        [
            {"source": {IRON: 10}, "source__valid": 1, "target": 0, "target__valid": 0},
            {"source": {}, "source__valid": 0, "target": 0, "target__valid": 0},
            {"source": {}, "source__valid": 0, "target": 1, "target__valid": 1},
            {
                "source": {COPPER: 40},
                "source__valid": 1,
                "target": 1,
                "target__valid": 1,
            },
            {"source": {}, "source__valid": 0, "target": 0, "target__valid": 0},
            {"source": {}, "source__valid": 0, "target": 1, "target__valid": 1},
        ],
        flush_ticks=1,
    )

    assert trace[3] == ({IRON: 10}, 1)
    # Source and target both occur at t=3. The target samples the pre-state, so the new copper
    # payload is not visible until a later target occurrence.
    assert trace[4] == ({IRON: 10}, 1)
    assert trace[6] == ({COPPER: 40}, 1)
    assert all(valid == 0 for index, (_, valid) in enumerate(trace) if index not in {3, 4, 6})
