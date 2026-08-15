from dataclasses import replace

import pytest

from factorio_circuit import Circuit, compile_circuit
from factorio_circuit.analysis.state_timing import (
    StateTimingError,
    analyze_normalized_state_timing,
)
from factorio_circuit.ir.physical import SignalId
from factorio_circuit.ir.semantic import VectorBinaryOp
from factorio_circuit.ir.state import state_transitions
from factorio_circuit.lowering.frontend_to_ir import normalize_module
from factorio_circuit.simulate.compare import assert_same_stream
from factorio_circuit.simulate.semantic import simulate_stream

from ..support.circuits import delayed_accumulator_window

IRON = SignalId("item", "iron-plate")


def test_elastic_transition_is_pinned_by_bracketing_reads() -> None:
    result = compile_circuit(delayed_accumulator_window(offset=3), optimize=False)
    timing = result.state_timing.registers[0]

    assert timing.period == 1
    assert timing.commit_offset == 3
    assert timing.earliest_transition_input_phase == 4
    assert timing.state_phase == 1
    assert timing.transition_input_phase == 4
    assert [item.physical_phase for item in timing.reads] == [4, 5]
    assert result.physical_circuit.output_phases == (4, 5)


@pytest.mark.parametrize("optimize", [False, True])
def test_bracketed_complex_update_matches_reference_state_stream(optimize: bool) -> None:
    result = compile_circuit(delayed_accumulator_window(offset=3), optimize=optimize)
    stream: list[dict[str, object]] = [
        {"data": {IRON: 1}, "clear": 0},
        {"data": {IRON: 2}, "clear": 0},
        {"data": {IRON: 4}, "clear": 0},
        {"data": {IRON: 8}, "clear": 0},
        {"data": {IRON: 16}, "clear": 1},
        {"data": {IRON: 32}, "clear": 0},
        {"data": {IRON: 64}, "clear": 0},
        {"data": {}, "clear": 0},
        {"data": {}, "clear": 0},
        {"data": {}, "clear": 0},
    ]
    assert_same_stream(result.semantic_ir, result.physical_circuit, stream)


def test_read_after_update_at_same_logical_step_is_rejected() -> None:
    c = Circuit("too_early")
    data = c.signals("data")
    enable = c.input("enable")
    memory = c.freeze("memory")
    memory.set(data, when=enable)
    c.output("new", memory.sample())

    with pytest.raises(StateTimingError, match="advance the logical step"):
        compile_circuit(c, optimize=False)


def test_extra_canonical_periodic_transition_drives_timing_and_simulation() -> None:
    c = Circuit("extra_canonical_transition")
    data = c.signals("data")
    memory = c.accumulator("memory")
    memory.add(data)
    c.step()
    c.output("memory", memory.sample())
    module = normalize_module(c.build())

    canonical = module.transitions[0]
    assert canonical.value is not None
    canonical_value = canonical.value
    extra = replace(
        canonical,
        order=1,
        value=VectorBinaryOp("+", canonical_value, canonical_value),
        when=canonical.when,
        legacy=None,
    )
    augmented = replace(module, transitions=(canonical, extra))

    baseline_timing = analyze_normalized_state_timing(module).registers[0]
    augmented_timing = analyze_normalized_state_timing(augmented).registers[0]

    assert len(state_transitions(augmented)) == 2
    assert (
        augmented_timing.earliest_transition_input_phase
        > baseline_timing.earliest_transition_input_phase
    )
    assert simulate_stream(augmented, [{"data": {IRON: 1}}]) == [({IRON: 3},)]


def test_previous_value_is_available_when_read_before_complex_update() -> None:
    c = Circuit("old_value")
    data = c.signals("data")
    enable = c.input("enable")
    memory = c.freeze("memory")

    old = memory.sample()
    complex_enable = (((enable + 1) * 3) - 3) > 0
    memory.set(data, when=complex_enable)
    c.step(1)
    new = memory.sample()
    c.output("old", old)
    c.output("new", new)

    result = compile_circuit(c, optimize=False)
    timing = result.state_timing.registers[0]
    assert timing.period == 1
    assert timing.commit_offset == 0
    assert timing.first_update_order == 1
    assert timing.reads[0].read.order == 0
    assert timing.reads[1].read.order == 2
    assert timing.reads[0].physical_phase < timing.reads[1].physical_phase


def test_read_cannot_split_current_accumulator_compound_transition() -> None:
    c = Circuit("split_update")
    data = c.signals("data")
    clear = c.input("clear")
    memory = c.accumulator("memory")
    memory.add(data)
    middle = memory.sample()
    memory.clear(when=clear)
    c.step(1)
    c.output("middle", middle)

    with pytest.raises(StateTimingError, match="inside one compound transition"):
        compile_circuit(c, optimize=False)


def test_future_sampled_state_update_marks_startup_semantics_as_unresolved() -> None:
    from factorio_circuit.simulate.semantic import simulate_stream as simulate_semantic_stream

    c = Circuit("future_state_startup")
    data = c.signals("data")
    memory = c.accumulator("memory")
    c.step(2)
    memory.add(data.sample())
    c.step(1)
    c.output("memory", memory.sample())

    result = compile_circuit(c, optimize=False)
    with pytest.raises(ValueError, match="startup/warm-up"):
        simulate_semantic_stream(result.semantic_ir, [{"data": {IRON: 1}}])
