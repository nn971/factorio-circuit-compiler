from factorio_circuit import Circuit
from factorio_circuit.experimental.temporal_hypergraph import (
    PeriodicDemand,
    TemporalHypergraph,
    TemporalOperation,
    TemporalValue,
    build_level_temporal_hypergraph,
    exact_scalar_materializations,
)
from factorio_circuit.ir.semantic import PayloadShape
from factorio_circuit.lowering.frontend_to_ir import lower_frontend


def _value(value_id: int, label: str) -> TemporalValue:
    return TemporalValue(
        id=value_id,
        label=label,
        shape=PayloadShape.SCALAR,
        clock=None,
        logical_offset=0,
    )


def test_semantic_builder_keeps_phases_unassigned() -> None:
    circuit = Circuit("experimental_temporal_graph")
    x = circuit.input("x")
    y = circuit.input("y")
    circuit.output("z", (x + y) * 3)

    graph = build_level_temporal_hypergraph(lower_frontend(circuit))

    assert len(graph.operations) == 2
    assert {operation.label for operation in graph.operations} == {"scalar:+", "scalar:*"}
    assert len(graph.observations) == 1
    assert graph.observations[0].label == "output:z"
    assert all(not hasattr(value, "phase") for value in graph.values)


def test_one_hold_replaces_long_delay_for_early_value() -> None:
    graph = TemporalHypergraph("hold", (_value(0, "x"),), (), ())

    plan = exact_scalar_materializations(
        graph,
        period=60,
        source_windows={0: ((2, 4),)},
        demands=(PeriodicDemand(0, 50, "late x"),),
    )

    assert plan.register_count == 1
    assert plan.materializations[0].value == 0
    assert plan.materializations[0].capture_phase == 2
    assert plan.materializations[0].valid_from == 3
    assert plan.materializations[0].valid_until == 60


def test_natural_stability_needs_no_materializer() -> None:
    graph = TemporalHypergraph("stable", (_value(0, "x"),), (), ())

    plan = exact_scalar_materializations(
        graph,
        period=60,
        source_windows={0: ((2, 60),)},
        demands=(PeriodicDemand(0, 50, "late x"),),
    )

    assert plan.register_count == 0


def test_exact_search_prefers_holding_derived_result_over_two_inputs() -> None:
    graph = TemporalHypergraph(
        "hold_output",
        (_value(0, "x"), _value(1, "y"), _value(2, "z")),
        (TemporalOperation(0, (0, 1), 2, (1, 1), "add"),),
        (),
    )

    plan = exact_scalar_materializations(
        graph,
        period=10,
        source_windows={0: ((0, 3),), 1: ((0, 3),)},
        demands=(PeriodicDemand(2, 8, "late z"),),
    )

    assert plan.register_count == 1
    assert plan.materializations[0].value == 2


def test_exact_search_prefers_one_shared_upstream_hold_for_two_results() -> None:
    graph = TemporalHypergraph(
        "hold_shared_input",
        (_value(0, "x"), _value(1, "a"), _value(2, "b")),
        (
            TemporalOperation(0, (0,), 1, (1,), "f"),
            TemporalOperation(1, (0,), 2, (1,), "g"),
        ),
        (),
    )

    plan = exact_scalar_materializations(
        graph,
        period=10,
        source_windows={0: ((0, 2),)},
        demands=(
            PeriodicDemand(1, 8, "late a"),
            PeriodicDemand(2, 8, "late b"),
        ),
    )

    assert plan.register_count == 1
    assert plan.materializations[0].value == 0
