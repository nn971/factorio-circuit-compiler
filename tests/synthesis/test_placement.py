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
            chunk_columns=2,
            chunk_rows=2,
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
