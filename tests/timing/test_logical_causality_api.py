import pytest

from factorio_circuit import Circuit
from factorio_circuit.analysis import (
    CausalityEdge,
    CausalityEdgeKind,
    CausalityGraph,
    ClockRelation,
    LogicalDependency,
    StateOrderError,
    analyze_causality,
    event_causality_graph,
    has_nonpositive_cycle,
    infer_commit_offset,
    periodic_causality_graph,
    state_read_occurrences,
)
from factorio_circuit.ir.semantic import (
    ClockId,
    ClockProvenance,
    Constant,
    VectorBinaryOp,
    VectorConstant,
)
from factorio_circuit.ir.state import FreezeRegister, FreezeSet, VectorRegisterRead
from factorio_circuit.lowering.frontend_to_ir import normalize_module

KIND = CausalityEdgeKind.ORDINARY_STATE_DEPENDENCY


def dependency(
    source: FreezeRegister,
    target: FreezeRegister,
    displacement: int,
) -> LogicalDependency:
    return LogicalDependency(source, target, KIND, displacement)


def test_logical_graph_needs_no_physical_target_latency() -> None:
    first = FreezeRegister("first")
    second = FreezeRegister("second")
    graph = CausalityGraph(
        (first, second),
        (
            dependency(first, second, 0),
            dependency(second, first, 1),
        ),
    )

    assert not has_nonpositive_cycle(graph)


def test_logical_legality_is_independent_of_timing_annotation() -> None:
    register = FreezeRegister("memory")
    logical = dependency(register, register, 1)
    fast = CausalityEdge(register, register, KIND, 1, 1)
    slow = CausalityEdge(register, register, KIND, 1, 100_000)

    assert fast.logical == logical
    assert slow.logical == logical
    assert not has_nonpositive_cycle(CausalityGraph((register,), (logical,)))
    assert not has_nonpositive_cycle(CausalityGraph((register,), (fast,)))
    assert not has_nonpositive_cycle(CausalityGraph((register,), (slow,)))


def test_zero_advance_cycle_is_rejected_with_pure_logical_dependencies() -> None:
    first = FreezeRegister("first")
    second = FreezeRegister("second")
    graph = CausalityGraph(
        (first, second),
        (
            dependency(first, second, 3),
            dependency(second, first, -3),
        ),
    )

    assert has_nonpositive_cycle(graph)


def test_known_cross_clock_cycle_is_not_summed_in_one_occurrence_coordinate() -> None:
    first = FreezeRegister("first")
    second = FreezeRegister("second")
    first_clock = ClockId("first-clock", ClockProvenance.INFERRED)
    second_clock = ClockId("second-clock", ClockProvenance.EXTERNAL_EVENT)
    graph = CausalityGraph(
        (first, second),
        (
            LogicalDependency(
                first,
                second,
                KIND,
                0,
                source_clock=first_clock,
                target_clock=second_clock,
            ),
            LogicalDependency(
                second,
                first,
                KIND,
                0,
                source_clock=second_clock,
                target_clock=first_clock,
            ),
        ),
    )

    assert all(edge.clock_relation is ClockRelation.CROSS for edge in graph.edges)
    assert not has_nonpositive_cycle(graph)


def test_acyclic_future_reference_does_not_create_a_feedback_violation() -> None:
    source = FreezeRegister("source")
    target = FreezeRegister("target")
    graph = CausalityGraph((source, target), (dependency(source, target, -4),))

    assert not has_nonpositive_cycle(graph)


def test_state_read_occurrences_preserve_parallel_semantic_dependencies() -> None:
    source = FreezeRegister("source")
    read = VectorRegisterRead(source, offset=2, order=0)

    assert state_read_occurrences(VectorBinaryOp("+", read, read)) == (read, read)


def test_commit_offset_is_inferred_without_target_latency() -> None:
    register = FreezeRegister("memory")
    before = VectorRegisterRead(register, offset=2, order=0)
    transition = FreezeSet(register, VectorConstant(()), Constant(1), order=1)
    after = VectorRegisterRead(register, offset=3, order=2)

    assert infer_commit_offset(register, (transition,), (before, after)) == 2


def test_commit_offset_rejects_a_read_inside_compound_transition() -> None:
    register = FreezeRegister("memory")
    first = FreezeSet(register, VectorConstant(()), Constant(1), order=1)
    second = FreezeSet(register, VectorConstant(()), Constant(1), order=3)
    split = VectorRegisterRead(register, offset=1, order=2)

    with pytest.raises(StateOrderError, match="inside one compound transition"):
        infer_commit_offset(register, (first, second), (split,))


def test_periodic_graph_is_derived_from_logical_register_reads() -> None:
    circuit = Circuit("logical_periodic_graph")
    data = circuit.signals("data")
    source = circuit.freeze("source")
    target = circuit.freeze("target")

    old_source = source.sample()
    source.set(data, when=1)
    target.set(old_source.step(2), when=1)
    circuit.step(1)
    circuit.output("target", target.sample())

    module = normalize_module(circuit.build())
    graph = periodic_causality_graph(module)
    matching = [
        edge
        for edge in graph.edges
        if edge.source.name == "source" and edge.target.name == "target"
    ]

    assert len(matching) == 1
    assert matching[0].kind is CausalityEdgeKind.ORDINARY_STATE_DEPENDENCY
    assert matching[0].logical_displacement == -1
    assert matching[0].clock_relation is ClockRelation.SAME
    assert matching[0].source_clock == matching[0].target_clock
    assert matching[0].source_clock is not None
    assert not hasattr(matching[0], "physical_latency")


def test_event_graph_uses_transition_occurrence_offset_without_latency() -> None:
    circuit = Circuit("logical_event_graph")
    data = circuit.signals("data")
    trigger = circuit.signal_event("trigger", guaranteed_min_separation=4)
    source = circuit.freeze("source")
    target = circuit.freeze("target")

    source.set(data, when=1)
    sampled = circuit.sample_on(source.sample(), trigger)
    target.set(sampled.step(2), when=1)

    module = circuit.build()
    graph = event_causality_graph(module)
    matching = [
        edge
        for edge in graph.edges
        if edge.source.name == "source" and edge.target.name == "target"
    ]

    assert len(matching) == 1
    assert matching[0].kind is CausalityEdgeKind.EVENT_STATE_DEPENDENCY
    assert matching[0].logical_displacement == 3
    assert matching[0].clock_relation is ClockRelation.CROSS
    assert matching[0].source_clock is not None
    assert matching[0].target_clock == trigger.clock.clock_id
    assert not hasattr(matching[0], "physical_latency")


def test_analyze_causality_runs_before_target_timing() -> None:
    circuit = Circuit("causality_phase")
    state = circuit.freeze("state")
    old_state = state.sample()
    state.set(old_state, when=1)
    circuit.step(1)
    circuit.output("state", state.sample())

    analysis = analyze_causality(circuit.build())

    assert analysis.causal
    assert len(analysis.periodic.edges) == 1
    assert analysis.periodic.edges[0].logical_displacement == 1
    assert analysis.periodic.edges[0].clock_relation is ClockRelation.SAME
    assert analysis.event.edges == ()
