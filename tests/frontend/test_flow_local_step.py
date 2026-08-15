import pytest

from factorio_circuit import Circuit, CircuitBuildError, Expr, SignalId, compile_circuit
from factorio_circuit.ir.semantic import (
    BinaryOp,
    FlowInputSample,
    FlowVectorInputSample,
    InputSample,
    VectorBinaryOp,
    VectorInputSample,
)
from factorio_circuit.ir.state import VectorRegisterRead
from factorio_circuit.lowering.frontend_to_ir import normalize_module
from factorio_circuit.simulate.compare import assert_same_stream

IRON = SignalId("item", "iron-plate")


def test_scalar_step_is_local_compositional_and_flow_backed() -> None:
    circuit = Circuit("scalar_local_step")
    source = circuit.input("source")
    current = source + 1
    later = current.step().step(2)

    assert isinstance(source, Expr)
    assert circuit.now.offset == 0
    assert later.step(0) is later

    circuit.output("current", current)
    circuit.output("later", later)
    raw = circuit.build()
    later_raw = raw.output.values[1]
    assert isinstance(later_raw, BinaryOp)
    assert isinstance(later_raw.left, InputSample)
    assert later_raw.left.offset == 3

    normalized = normalize_module(raw)
    current_flow = normalized.output.values[0].flow  # type: ignore[attr-defined]
    later_flow = normalized.output.values[1].flow  # type: ignore[attr-defined]
    assert current_flow.logical_offset == 0
    assert later_flow.logical_offset == 3
    assert current_flow.clock == later_flow.clock
    assert isinstance(normalized.output.values[1].left, FlowInputSample)  # type: ignore[attr-defined]


def test_vector_step_is_local_compositional_and_keeps_vectors_packed() -> None:
    circuit = Circuit("vector_local_step")
    source = circuit.signals("source")
    current = source + circuit.constant_signals({IRON: 1})
    later = current.step(2).step()

    assert circuit.now.offset == 0
    assert later.step(0) is later

    circuit.output("current", current)
    circuit.output("later", later)
    raw = circuit.build()
    later_raw = raw.output.values[1]
    assert isinstance(later_raw, VectorBinaryOp)
    assert isinstance(later_raw.left, VectorInputSample)
    assert later_raw.left.offset == 3

    normalized = normalize_module(raw)
    later_normalized = normalized.output.values[1]
    assert isinstance(later_normalized, VectorBinaryOp)
    assert later_normalized.flow is not None
    assert later_normalized.flow.logical_offset == 3
    assert isinstance(later_normalized.left, FlowVectorInputSample)


def test_register_step_reindexes_the_read_without_advancing_circuit_cursor() -> None:
    circuit = Circuit("register_local_step")
    memory = circuit.freeze("memory")

    current = memory.sample()
    later = current.step(2)

    assert circuit.now.offset == 0
    assert isinstance(current.ir, VectorRegisterRead)
    assert isinstance(later.ir, VectorRegisterRead)
    assert current.ir.offset == 0
    assert later.ir.offset == 2
    assert current.ir.register == later.ir.register
    assert current.ir.order == later.ir.order


def _compile_state_read(*, local_step: bool):
    circuit = Circuit("state_local" if local_step else "state_cursor")
    data = circuit.signals("data")
    memory = circuit.freeze("memory")
    memory.set(data, when=1)
    if local_step:
        new_value = memory.sample().step()
        assert circuit.now.offset == 0
    else:
        circuit.step()
        new_value = memory.sample()
    circuit.output("memory", new_value)
    return compile_circuit(circuit, optimize=False)


def test_flow_local_step_matches_legacy_cursor_for_next_state_observation() -> None:
    local = _compile_state_read(local_step=True)
    legacy = _compile_state_read(local_step=False)

    local_timing = local.state_timing.registers[0]
    legacy_timing = legacy.state_timing.registers[0]
    assert local_timing.period == legacy_timing.period
    assert local_timing.commit_offset == legacy_timing.commit_offset
    assert local_timing.state_phase == legacy_timing.state_phase
    assert local.physical_circuit.output_phases == legacy.physical_circuit.output_phases

    stream = [
        {"data": {IRON: 1}},
        {"data": {IRON: 2}},
        {"data": {IRON: 3}},
    ]
    assert_same_stream(local.semantic_ir, local.physical_circuit, stream)
    assert_same_stream(legacy.semantic_ir, legacy.physical_circuit, stream)


@pytest.mark.parametrize("bad", [-1, True, 1.5])
def test_flow_local_step_rejects_invalid_displacements(bad: object) -> None:
    circuit = Circuit("bad_local_step")
    source = circuit.input("source")
    with pytest.raises(CircuitBuildError, match="non-negative integer"):
        source.step(bad)  # type: ignore[arg-type]


def test_event_flow_step_waits_for_event_occurrence_offset_representation() -> None:
    circuit = Circuit("event_step_boundary")
    event = circuit.event("event", guaranteed_min_separation=1)
    derived = event + 1

    with pytest.raises(CircuitBuildError, match="Event values"):
        derived.step()
