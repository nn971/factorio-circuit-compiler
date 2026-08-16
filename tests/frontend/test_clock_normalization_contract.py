import pytest

from factorio_circuit import Circuit, SignalId, compile_circuit
from factorio_circuit.analysis.state_timing import StateTimingPlan, analyze_normalized_state_timing
from factorio_circuit.ir.semantic import (
    BinaryOp,
    CanonicalInvariantError,
    CircuitModule,
    Clock,
    ClockProvenance,
    Constant,
    Flow,
    Input,
    PayloadShape,
    ReturnValue,
    TemporalModality,
)
from factorio_circuit.lowering.open_vector_pipeline import lower_normalized_vectors
from factorio_circuit.optimize.common_subexpr import eliminate_common_subexpressions
from factorio_circuit.simulate.semantic import simulate_normalized_stream

IRON = SignalId("item", "iron-plate")


def _shared_dag_circuit() -> Circuit:
    circuit = Circuit("shared_clock_context")
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


def _sole_link_circuit() -> Circuit:
    circuit = Circuit("sole_state_link")
    data = circuit.signals("data")
    first = circuit.freeze("first")
    second = circuit.freeze("second")
    old_first = first.sample()
    first.set(data, when=1)
    second.set(data, when=old_first.signal(IRON) * 0)
    circuit.step(1)
    circuit.output("result", second.sample().signal(IRON))
    return circuit


def test_optimizer_rebuilds_clock_domains_after_removing_a_state_link() -> None:
    result = compile_circuit(_sole_link_circuit(), optimize=True)

    timings = {item.register.name: item for item in result.state_timing.registers}
    assert len(result.state_timing.domains) == 2
    assert timings["first"].clock_domain != timings["second"].clock_domain

    output = result.optimized_ir.output.values[0]
    register_clocks = dict(result.optimized_ir.register_clocks)
    second = next(
        register for register in result.optimized_ir.state_registers if register.name == "second"
    )
    assert output.flow.clock == register_clocks[second]  # type: ignore[attr-defined]


def test_canonical_entry_points_reject_raw_frontend_values() -> None:
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


def test_flow_clock_and_offset_are_part_of_common_subexpression_identity() -> None:
    source = Input("source")
    clock_a = Clock("a", ClockProvenance.INFERRED)
    clock_b = Clock("b", ClockProvenance.INFERRED)
    first = BinaryOp(
        "+",
        source,
        Constant(1),
        flow=Flow("first", PayloadShape.SCALAR, TemporalModality.LEVEL, clock_a, 0),
    )
    second = BinaryOp(
        "+",
        source,
        Constant(1),
        flow=Flow("second", PayloadShape.SCALAR, TemporalModality.LEVEL, clock_b, 1),
    )
    module = CircuitModule("cse_flow", (source,), (first, second), ReturnValue((first, second)))

    optimized = eliminate_common_subexpressions(module)
    left, right = optimized.output.values
    assert left is not right
    assert left.flow.clock != right.flow.clock  # type: ignore[attr-defined]
    assert left.flow.logical_offset != right.flow.logical_offset  # type: ignore[attr-defined]


def test_clock_contract_does_not_change_structural_clock_identity() -> None:
    clock = Clock("event", ClockProvenance.EXTERNAL_EVENT, guaranteed_min_separation=4)
    refined = Clock("event", ClockProvenance.EXTERNAL_EVENT, guaranteed_min_separation=8)
    assert refined == clock
    assert hash(refined) == hash(clock)
