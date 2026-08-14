from dataclasses import FrozenInstanceError

import pytest

from factorio_circuit import Circuit, SignalId
from factorio_circuit.analysis.causality import (
    CausalityEdge,
    CausalityEdgeKind,
    CausalityGraph,
    has_nonpositive_cycle,
)
from factorio_circuit.analysis.state_timing import (
    StateTimingError,
    _analyze_register_semantics,
    _causality_graph,
    _collect_state_reads,
    _RegisterSpec,
    _Requirement,
    analyze_state_timing,
)
from factorio_circuit.ir.semantic import CircuitModule, Constant, ReturnValue, VectorConstant
from factorio_circuit.ir.state import (
    AccumulatorRegister,
    FreezeRegister,
    FreezeSet,
    VectorRegisterRead,
)

KIND = CausalityEdgeKind.ORDINARY_STATE_DEPENDENCY


def edge(
    source: FreezeRegister,
    target: FreezeRegister,
    displacement: int,
    latency: int,
) -> CausalityEdge:
    return CausalityEdge(source, target, KIND, displacement, latency)


def test_graph_is_immutable_ordered_and_keeps_parallel_edges() -> None:
    source = FreezeRegister("source")
    target = FreezeRegister("target")
    direct = edge(source, target, 2, 1)
    control = edge(source, target, 2, 3)
    graph = CausalityGraph((source, target), (direct, control))

    assert graph.registers == (source, target)
    assert graph.edges == (direct, control)
    assert graph.edges[0].kind is KIND
    assert graph.edges[0].physical_latency != graph.edges[1].physical_latency
    with pytest.raises(FrozenInstanceError):
        graph.edges = ()  # type: ignore[misc]


def test_graph_rejects_edge_endpoints_outside_the_listed_registers() -> None:
    listed = FreezeRegister("listed")
    unlisted = AccumulatorRegister("listed")

    with pytest.raises(ValueError, match="edge endpoints"):
        CausalityGraph(
            (listed,),
            (CausalityEdge(unlisted, listed, KIND, 1, 1),),
        )
    with pytest.raises(ValueError, match="edge endpoints"):
        CausalityGraph(
            (listed,),
            (CausalityEdge(listed, unlisted, KIND, 1, 1),),
        )


def test_register_values_not_names_define_graph_vertices() -> None:
    accumulator = AccumulatorRegister("same-name")
    freeze = FreezeRegister("same-name")
    graph = CausalityGraph(
        (accumulator, freeze),
        (CausalityEdge(accumulator, freeze, KIND, 0, 1),),
    )

    assert not has_nonpositive_cycle(graph)


def test_requirement_adapter_projects_direct_and_control_edges_without_external_leaves() -> None:
    source = FreezeRegister("source")
    target = FreezeRegister("target")
    spec = _RegisterSpec(
        register=target,
        operations=(),
        reads=(),
        commit_offset=3,
        first_update_order=1,
        last_update_order=1,
        requirements=(
            _Requirement(source, logical_offset=2, latency=0),
            _Requirement(source, logical_offset=2, latency=2),
            _Requirement(None, logical_offset=4, latency=20),
        ),
    )

    source_spec = _RegisterSpec(
        register=source,
        operations=(),
        reads=(),
        commit_offset=0,
        first_update_order=1,
        last_update_order=1,
        requirements=(),
    )
    graph = _causality_graph((source_spec, spec))

    assert len(graph.edges) == 2
    assert [(item.logical_displacement, item.physical_latency) for item in graph.edges] == [
        (2, 1),
        (2, 3),
    ]
    assert all(item.kind is KIND for item in graph.edges)


def test_frontend_direct_and_control_requirements_become_ordered_parallel_edges() -> None:
    signal = SignalId("virtual", "signal-test")
    circuit = Circuit("direct_control_edges")
    data = circuit.signals("data")
    enable = circuit.input("enable")
    source = circuit.freeze("source")
    target = circuit.freeze("target")

    old_source = source.sample()
    source.set(data, when=enable)
    target.set(old_source, when=old_source.signal(signal) != 0)
    circuit.step(1)
    circuit.output("target", target.sample())
    module = circuit.build()

    reads = _collect_state_reads(module)
    specs = tuple(
        _analyze_register_semantics(
            register,
            tuple(
                operation for operation in module.state_operations if operation.register == register
            ),
            tuple(read for read in reads if read.register == register),
        )
        for register in module.state_registers
    )
    graph = _causality_graph(specs)
    edges = [edge for edge in graph.edges if edge.source.name == "source"]

    assert [
        (item.target.name, item.logical_displacement, item.physical_latency) for item in edges
    ] == [
        ("target", 1, 1),
        ("target", 1, 3),
    ]


def test_positive_self_cycle_is_legal_even_with_long_physical_latency() -> None:
    register = FreezeRegister("memory")
    graph = CausalityGraph((register,), (edge(register, register, 1, 1000),))

    assert not has_nonpositive_cycle(graph)


@pytest.mark.parametrize("displacement", [0, -1])
def test_zero_and_negative_self_loops_are_noncausal(displacement: int) -> None:
    register = FreezeRegister("memory")

    assert has_nonpositive_cycle(
        CausalityGraph((register,), (edge(register, register, displacement, 1),))
    )


def test_positive_cycle_with_a_zero_displacement_edge_is_legal() -> None:
    first = FreezeRegister("first")
    second = FreezeRegister("second")
    graph = CausalityGraph(
        (first, second),
        (edge(first, second, 0, 100), edge(second, first, 1, 1)),
    )

    assert not has_nonpositive_cycle(graph)


def test_state_timing_accepts_a_positive_multi_register_cycle_with_zero_edge() -> None:
    first = FreezeRegister("first")
    second = FreezeRegister("second")
    first_read = VectorRegisterRead(first, offset=0, order=0, name="first")
    second_read = VectorRegisterRead(second, offset=1, order=2, name="second")
    module = CircuitModule(
        name="positive_cycle",
        inputs=(),
        operations=(),
        output=ReturnValue((VectorConstant(()),)),
        state_registers=(first, second),
        state_operations=(
            FreezeSet(first, second_read, Constant(1), order=1),
            FreezeSet(second, first_read, Constant(1), order=1),
        ),
    )

    plan = analyze_state_timing(module)

    assert plan.uniform_period == 2


def test_zero_total_cycle_is_rejected_independently_of_physical_latency() -> None:
    first = FreezeRegister("first")
    second = FreezeRegister("second")
    graph = CausalityGraph(
        (first, second),
        (edge(first, second, 4, 1), edge(second, first, -4, 10000)),
    )

    assert has_nonpositive_cycle(graph)


def test_disconnected_nonpositive_cycle_is_still_rejected() -> None:
    first = FreezeRegister("bad-first")
    second = FreezeRegister("bad-second")
    third = FreezeRegister("valid-first")
    fourth = FreezeRegister("valid-second")
    graph = CausalityGraph(
        (first, second, third, fourth),
        (
            edge(first, second, 0, 1),
            edge(second, first, 0, 1),
            edge(third, fourth, 2, 1000),
        ),
    )

    assert has_nonpositive_cycle(graph)


def test_parallel_edges_reject_only_when_the_bad_alternative_is_present() -> None:
    first = FreezeRegister("first")
    second = FreezeRegister("second")
    return_edge = edge(second, first, 0, 1)
    good_edge = edge(first, second, 1, 1)
    bad_edge = edge(first, second, 0, 1000)

    assert has_nonpositive_cycle(
        CausalityGraph((first, second), (good_edge, bad_edge, return_edge))
    )
    assert not has_nonpositive_cycle(CausalityGraph((first, second), (good_edge, return_edge)))


def test_acyclic_long_latency_graph_is_legal() -> None:
    first = FreezeRegister("first")
    second = FreezeRegister("second")
    third = FreezeRegister("third")
    graph = CausalityGraph(
        (first, second, third),
        (edge(first, second, 0, 100000), edge(second, third, -20, 200000)),
    )

    assert not has_nonpositive_cycle(graph)


def test_state_timing_keeps_legacy_noncausal_diagnostic() -> None:
    first = FreezeRegister("first")
    second = FreezeRegister("second")
    first_read = VectorRegisterRead(first, offset=1, order=2, name="first")
    second_read = VectorRegisterRead(second, offset=1, order=2, name="second")
    module = CircuitModule(
        name="zero_cycle",
        inputs=(),
        operations=(),
        output=ReturnValue(
            (
                VectorConstant(
                    (),
                ),
            )
        ),
        state_registers=(first, second),
        state_operations=(
            FreezeSet(first, second_read, Constant(1), order=1),
            FreezeSet(second, first_read, Constant(1), order=1),
        ),
    )

    with pytest.raises(StateTimingError, match="noncausal/zero-distance physical cycle"):
        analyze_state_timing(module)


def test_state_timing_reports_only_the_first_offending_domain() -> None:
    bad_first = FreezeRegister("bad-first")
    bad_second = FreezeRegister("bad-second")
    valid = FreezeRegister("valid")
    bad_first_read = VectorRegisterRead(bad_first, offset=1, order=2, name="bad-first")
    bad_second_read = VectorRegisterRead(bad_second, offset=1, order=2, name="bad-second")
    valid_read = VectorRegisterRead(valid, offset=0, order=0, name="valid")
    module = CircuitModule(
        name="domain_specific_cycle",
        inputs=(),
        operations=(),
        output=ReturnValue((VectorConstant(()),)),
        state_registers=(bad_first, bad_second, valid),
        state_operations=(
            FreezeSet(bad_first, bad_second_read, Constant(1), order=1),
            FreezeSet(bad_second, bad_first_read, Constant(1), order=1),
            FreezeSet(valid, valid_read, Constant(1), order=1),
        ),
    )

    with pytest.raises(StateTimingError) as error:
        analyze_state_timing(module)

    message = str(error.value)
    assert message.startswith(
        "state recurrence has no finite logical clock period: ordinary same-step dependencies "
        "form a noncausal/zero-distance physical cycle in domain {bad-first, bad-second}"
    )
    assert "valid" not in message
