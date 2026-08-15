from factorio_circuit.analysis import (
    CausalityEdge,
    CausalityEdgeKind,
    CausalityGraph,
    LogicalDependency,
    has_nonpositive_cycle,
)
from factorio_circuit.ir.state import FreezeRegister

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


def test_acyclic_future_reference_does_not_create_a_feedback_violation() -> None:
    source = FreezeRegister("source")
    target = FreezeRegister("target")
    graph = CausalityGraph((source, target), (dependency(source, target, -4),))

    assert not has_nonpositive_cycle(graph)
