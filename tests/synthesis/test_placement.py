import pytest

from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import ConstantCombinator, PhysicalCircuit
from factorio_circuit.synthesis.physical import synthesize_layout
from factorio_circuit.synthesis.placement import (
    PlacementOptions,
    place_physical_circuit,
    placement_metrics,
)


def _single_net_fixture(
    count: int,
) -> tuple[
    abstract.AbstractPhysicalCircuit,
    PhysicalCircuit,
    dict[int, int],
]:
    abstract_circuit = abstract.AbstractPhysicalCircuit("placement")
    abstract_circuit.entities.extend(
        abstract.ConstantCombinator(entity_id, annotation_only=True)
        for entity_id in range(1, count + 1)
    )
    abstract_circuit.nets.append(
        abstract.AbstractNet(
            1,
            (),
            tuple(
                abstract.Endpoint(entity_id, abstract.Connector.SINGLE)
                for entity_id in range(1, count + 1)
            ),
        )
    )
    physical = PhysicalCircuit(
        "placement",
        entities=[
            ConstantCombinator(entity_id, annotation_only=True) for entity_id in range(1, count + 1)
        ],
    )
    return abstract_circuit, physical, {1: 1}


def test_net_aware_placement_makes_large_shared_net_reach_connected() -> None:
    abstract_circuit, physical, groups = _single_net_fixture(32)

    positions = place_physical_circuit(
        physical,
        abstract_circuit,
        groups,
        safe_wire_span=7.0,
        options=PlacementOptions(iterations=0, reserve_corridors=False),
    )
    metrics = placement_metrics(
        abstract_circuit,
        groups,
        positions,
        safe_wire_span=7.0,
    )

    assert metrics.disconnected_net_components == 0
    assert metrics.estimated_relays == 0


def test_entity_anchor_is_preserved_exactly() -> None:
    abstract_circuit, physical, groups = _single_net_fixture(6)
    anchor = (20.0, -4.0)

    positions = place_physical_circuit(
        physical,
        abstract_circuit,
        groups,
        safe_wire_span=7.0,
        options=PlacementOptions(anchors={1: anchor}, iterations=0),
    )

    assert positions[1] == anchor


def test_overlapping_anchors_are_rejected() -> None:
    abstract_circuit, physical, groups = _single_net_fixture(3)

    with pytest.raises(ValueError, match="anchors overlap"):
        place_physical_circuit(
            physical,
            abstract_circuit,
            groups,
            safe_wire_span=7.0,
            options=PlacementOptions(
                anchors={1: (0.0, 0.0), 2: (0.0, 0.0)},
                iterations=0,
            ),
        )


def test_reserved_corridors_expand_the_placement_lattice() -> None:
    abstract_circuit, physical, groups = _single_net_fixture(40)
    dense = place_physical_circuit(
        physical,
        abstract_circuit,
        groups,
        safe_wire_span=7.0,
        options=PlacementOptions(reserve_corridors=False, iterations=0),
    )
    corridor = place_physical_circuit(
        physical,
        abstract_circuit,
        groups,
        safe_wire_span=7.0,
        options=PlacementOptions(
            reserve_corridors=True,
            block_width_tiles=4,
            block_height_tiles=2,
            corridor_width=3.0,
            iterations=0,
        ),
    )

    dense_width = max(x for x, _ in dense.values()) - min(x for x, _ in dense.values())
    dense_height = max(y for _, y in dense.values()) - min(y for _, y in dense.values())
    corridor_width = max(x for x, _ in corridor.values()) - min(x for x, _ in corridor.values())
    corridor_height = max(y for _, y in corridor.values()) - min(y for _, y in corridor.values())

    assert corridor_width > dense_width
    assert corridor_height > dense_height


def test_synthesis_chooses_post_placement_spanning_tree_for_physical_net() -> None:
    abstract_circuit, _physical, _groups = _single_net_fixture(20)

    layout = synthesize_layout(
        abstract_circuit,
        placement=PlacementOptions(iterations=0, reserve_corridors=False),
    )

    assert layout.relays == ()
    assert len(layout.wires) == 19


def test_default_io_markers_are_ordered_on_left_and_right_perimeters() -> None:
    from factorio_circuit import Circuit, compile_circuit

    circuit = Circuit("anchored_io")
    inputs = [circuit.input(f"x{index}") for index in range(4)]
    for index, value in enumerate(inputs):
        circuit.output(f"y{index}", value + 1)

    result = compile_circuit(circuit)
    positions = result.layout.positions
    physical = result.physical_circuit
    input_positions = [positions[port.marker_entity] for port in physical.inputs]
    output_positions = [positions[port.marker_entity] for port in physical.outputs]
    io_ids = {port.marker_entity for port in (*physical.inputs, *physical.outputs)}
    implementation_x = [
        positions[entity.id][0] for entity in physical.entities if entity.id not in io_ids
    ]

    assert [position[1] for position in input_positions] == [0.0, 1.0, 2.0, 3.0]
    assert [position[1] for position in output_positions] == [0.0, 1.0, 2.0, 3.0]
    assert len({position[0] for position in input_positions}) == 1
    assert len({position[0] for position in output_positions}) == 1
    assert input_positions[0][0] < min(implementation_x)
    assert output_positions[0][0] > max(implementation_x)


def test_default_corridor_geometry_is_substation_pitch_aligned_and_relay_open() -> None:
    from factorio_circuit.synthesis.placement import _candidate_grid

    options = PlacementOptions(iterations=0)
    grid = _candidate_grid(300, 1, options)
    slots = set(grid.slots)

    # Horizontal 2x1 combinator centres: 0..14, then a two-tile gap, then 18.
    assert (14.0, 0.0) in slots
    assert (16.0, 0.0) not in slots
    assert (18.0, 0.0) in slots
    # Vertical 1-tile rows: 0..15, then a two-tile gap, then 18.
    assert (0.0, 15.0) in slots
    assert (0.0, 16.0) not in slots
    assert (0.0, 17.0) not in slots
    assert (0.0, 18.0) in slots
    assert options.block_width_tiles == 16
    assert options.block_height_tiles == 16
    assert options.corridor_width == 2.0

    # Corridors are reserved from ordinary implementation placement, not from layout-only relays.
    assert grid.relay_forbidden_areas == ()
