from dataclasses import replace

import pytest

from factorio_circuit import Circuit, EventScheduleError, SignalId, compile_circuit
from factorio_circuit.analysis.state_timing import (
    StateTimingPlan,
    analyze_normalized_state_timing,
)
from factorio_circuit.ir.semantic import (
    BinaryOp,
    CanonicalInvariantError,
    CircuitModule,
    Clock,
    ClockProvenance,
    Constant,
    EventInput,
    Flow,
    Input,
    PayloadShape,
    ReturnValue,
    TemporalModality,
)
from factorio_circuit.ir.state import FreezeCapture
from factorio_circuit.lowering.open_vector_pipeline import (
    lower_normalized_vectors,
    lower_vectors,
)
from factorio_circuit.optimize.common_subexpr import eliminate_common_subexpressions
from factorio_circuit.simulate.events import (
    EventOccurrence,
    EventSchedule,
    simulate_events,
)
from factorio_circuit.simulate.semantic import simulate_normalized_stream

IRON = SignalId("item", "iron-plate")


def _shared_dag_circuit() -> Circuit:
    circuit = Circuit("shared_gate1")
    scalar = circuit.input("scalar")
    vector = circuit.signals("vector")
    shared_scalar = scalar + 1
    shared_vector = vector + circuit.constant_signals({IRON: 1})
    first = circuit.accumulator("first")
    second = circuit.accumulator("second")
    first.add(shared_vector, when=shared_scalar)
    second.add(shared_vector, when=shared_scalar)
    circuit.output("scalar", shared_scalar)
    circuit.output("lane", shared_vector.signal(IRON))
    return circuit


@pytest.mark.parametrize("optimize", [False, True])
def test_shared_level_dags_are_contextualized_per_state_consumer(optimize: bool) -> None:
    result = compile_circuit(_shared_dag_circuit(), optimize=optimize)

    first, second = result.optimized_ir.state_operations[:2]
    assert first.when.flow.clock != second.when.flow.clock
    assert first.value.flow.clock != second.value.flow.clock
    assert all(
        getattr(value, "flow", None) is not None for value in result.semantic_ir.output.values
    )


def test_optimizer_recontextualizes_a_level_node_after_clock_linkage_is_removed() -> None:
    circuit = Circuit("post_linkage")
    scalar = circuit.input("scalar")
    register = circuit.accumulator("memory")
    shared = scalar + 0
    register.add(circuit.constant_signals({}), when=shared)
    circuit.output("scalar", shared)

    result = compile_circuit(circuit, optimize=True)
    assert result.optimized_ir.output.values[0].flow.clock.identity == "post_linkage:level"  # type: ignore[attr-defined]
    assert (
        result.optimized_ir.state_operations[0].when.flow.clock.identity
        == "post_linkage:state:memory"
    )


def _sole_link_circuit(*, vector_output: bool) -> Circuit:
    circuit = Circuit("sole_link_vector" if vector_output else "sole_link_scalar")
    data = circuit.signals("data")
    first = circuit.freeze("first")
    second = circuit.freeze("second")
    old_first = first.sample()
    first.set(data, when=1)
    # This is the only link between the two state domains.  Optimization removes it.
    second.set(data, when=old_first.signal(IRON) * 0)
    circuit.step(1)
    new_second = second.sample()
    circuit.output("result", new_second if vector_output else new_second.signal(IRON))
    return circuit


@pytest.mark.parametrize("vector_output", [False, True])
def test_optimized_removal_of_sole_state_link_rebuilds_state_read_flows(
    vector_output: bool,
) -> None:
    result = compile_circuit(_sole_link_circuit(vector_output=vector_output), optimize=True)

    timings = {item.register.name: item for item in result.state_timing.registers}
    assert len(result.state_timing.domains) == 2
    assert timings["first"].clock_domain != timings["second"].clock_domain
    output = result.optimized_ir.output.values[0]
    output_flow = output.flow  # type: ignore[attr-defined]
    register_clocks = dict(result.optimized_ir.register_clocks)
    second_register = next(
        register for register in result.optimized_ir.state_registers if register.name == "second"
    )
    assert output_flow.clock == register_clocks[second_register]


def test_normalized_entry_points_reject_raw_legacy_values() -> None:
    circuit = Circuit("raw_boundary")
    value = circuit.input("value")
    circuit.output("value", value)
    module = circuit.build()

    with pytest.raises(CanonicalInvariantError):
        analyze_normalized_state_timing(module)
    with pytest.raises(CanonicalInvariantError):
        simulate_normalized_stream(module, [{"value": 1}])
    with pytest.raises(CanonicalInvariantError):
        lower_normalized_vectors(module, enable_packing=False, state_timing=StateTimingPlan((), ()))
    with pytest.raises(CanonicalInvariantError):
        lower_vectors(module, enable_packing=False, state_timing=StateTimingPlan((), ()))


def test_flow_clock_and_offset_prevent_common_subexpression_aliasing() -> None:
    left = Input("left")
    clock_a = Clock("a", ClockProvenance.INFERRED)
    clock_b = Clock("b", ClockProvenance.INFERRED)
    first = BinaryOp(
        "+",
        left,
        Constant(1),
        flow=Flow("first", PayloadShape.SCALAR, TemporalModality.LEVEL, clock_a, 0),
    )
    second = BinaryOp(
        "+",
        left,
        Constant(1),
        flow=Flow("second", PayloadShape.SCALAR, TemporalModality.LEVEL, clock_b, 1),
    )
    module = CircuitModule("cse_flow", (left,), (first, second), ReturnValue((first, second)))

    optimized = eliminate_common_subexpressions(module)
    assert optimized.output.values[0] is not optimized.output.values[1]
    assert optimized.output.values[0].flow.clock != optimized.output.values[1].flow.clock  # type: ignore[attr-defined]
    assert (
        optimized.output.values[0].flow.logical_offset
        != optimized.output.values[1].flow.logical_offset
    )  # type: ignore[attr-defined]


def test_equal_identity_event_schedule_uses_declared_contract() -> None:
    circuit = Circuit("event_contract")
    event = circuit.event("done", guaranteed_min_separation=3)
    register = circuit.freeze("held")
    register.capture_on(
        event,
        circuit.constant_signals({IRON: 1}),
        required_min_separation=2,
    )
    module = circuit.build()
    declared = module.event_inputs[0]
    weakened = EventInput(
        declared.name,
        declared.payload_shape,
        Clock(declared.clock.identity, declared.clock.provenance, 1),
    )
    operation = module.event_state_operations[0]
    assert isinstance(operation, FreezeCapture)
    substituted = replace(operation, trigger=weakened)
    substituted_module = CircuitModule(
        module.name,
        module.inputs,
        module.operations,
        module.output,
        module.vector_inputs,
        module.state_registers,
        module.state_operations,
        module.event_inputs,
        (substituted,),
        module.sample_on_crossings,
        module.register_clocks,
    )

    with pytest.raises(EventScheduleError):
        simulate_events(
            substituted_module,
            [{}],
            [EventSchedule(weakened, (EventOccurrence(0, 1), EventOccurrence(2, 2)))],
            stop_timestamp=3,
        )

    with pytest.raises(EventScheduleError, match="conflicting Event clock contract"):
        simulate_events(
            substituted_module,
            [{}],
            [EventSchedule(weakened, (EventOccurrence(0, 1), EventOccurrence(3, 2)))],
            stop_timestamp=4,
        )
