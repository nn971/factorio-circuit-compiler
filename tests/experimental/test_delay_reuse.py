from factorio_circuit import Circuit, lower_to_abstract_physical
from factorio_circuit.experimental.delay_reuse import project_delay_reuse


def _unequal_depth() -> Circuit:
    circuit = Circuit("delay_reuse")
    a = circuit.input("a")
    b = circuit.input("b")
    x = a + 1
    y = x * 3
    circuit.output("z", y - b)
    return circuit


def test_delay_chain_projects_to_one_temporal_hold() -> None:
    lowered = lower_to_abstract_physical(_unequal_depth(), optimize=False)
    projection = project_delay_reuse(lowered.abstract_physical, period=10)

    assert projection.delay_count == 2
    assert projection.scalar_delays == 2
    assert projection.vector_delays == 0
    assert len(projection.components) == 1
    assert projection.components[0].delay_count == 2
    assert projection.components[0].max_depth == 2
    assert projection.projected_holds == 1
    assert projection.removable_delays == 2
    assert projection.remaining_delays == 0


def test_component_must_fit_inside_inactive_interval() -> None:
    lowered = lower_to_abstract_physical(_unequal_depth(), optimize=False)
    projection = project_delay_reuse(lowered.abstract_physical, period=2)

    assert projection.delay_count == 2
    assert projection.projected_holds == 0
    assert projection.removable_delays == 0
    assert projection.remaining_delays == 2
    assert len(projection.ineligible_components) == 1
