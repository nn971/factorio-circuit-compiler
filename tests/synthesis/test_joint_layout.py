from math import sqrt

from factorio_circuit.blueprint.routing import validate_wire_spans
from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import ConstantCombinator, PhysicalCircuit, WireColor
from factorio_circuit.synthesis.incremental_joint_layout import refine_incremental_joint_layout
from factorio_circuit.synthesis.joint_layout import refine_joint_layout
from factorio_circuit.synthesis.placement import PlacementOptions


def test_joint_layout_shares_one_relay_across_three_terminal_net() -> None:
    abstract_circuit = abstract.AbstractPhysicalCircuit("shared_relay")
    abstract_circuit.entities.extend(
        abstract.ConstantCombinator(entity_id, annotation_only=True) for entity_id in (1, 2, 3)
    )
    abstract_circuit.nets.append(
        abstract.AbstractNet(
            1,
            (),
            tuple(
                abstract.Endpoint(entity_id, abstract.Connector.SINGLE) for entity_id in (1, 2, 3)
            ),
        )
    )
    physical = PhysicalCircuit(
        "shared_relay",
        entities=[ConstantCombinator(entity_id, annotation_only=True) for entity_id in (1, 2, 3)],
    )
    positions = {
        1: (-4.0, 0.0),
        2: (4.0, 0.0),
        3: (0.0, 4.0 * sqrt(3.0)),
    }

    result = refine_joint_layout(
        physical,
        abstract_circuit,
        {1: 1},
        {1: WireColor.RED},
        positions,
        safe_wire_span=7.0,
        options=PlacementOptions(
            iterations=0,
            reserve_corridors=False,
            anchor_io=False,
        ),
    )

    assert len(result.routing.relays) == 1
    assert len(result.routing.wires) == 3
    all_positions = dict(result.positions)
    all_positions.update({relay.entity_id: relay.position for relay in result.routing.relays})
    validate_wire_spans(result.routing.wires, all_positions, maximum_span=7.0)


def test_incremental_joint_layout_runs_without_relay_bearing_nets() -> None:
    abstract_circuit = abstract.AbstractPhysicalCircuit("incremental_direct")
    abstract_circuit.entities.extend(
        abstract.ConstantCombinator(entity_id, annotation_only=True) for entity_id in (1, 2)
    )
    abstract_circuit.nets.append(
        abstract.AbstractNet(
            1,
            (),
            tuple(
                abstract.Endpoint(entity_id, abstract.Connector.SINGLE) for entity_id in (1, 2)
            ),
        )
    )
    physical = PhysicalCircuit(
        "incremental_direct",
        entities=[ConstantCombinator(entity_id, annotation_only=True) for entity_id in (1, 2)],
    )

    result = refine_incremental_joint_layout(
        physical,
        abstract_circuit,
        {1: 1},
        {1: WireColor.RED},
        {1: (-0.5, 0.0), 2: (0.5, 0.0)},
        safe_wire_span=7.0,
        options=PlacementOptions(
            iterations=32,
            reserve_corridors=False,
            anchor_io=False,
            target_fill=0.5,
        ),
    )

    assert result.routing.relays == ()
    assert len(result.routing.wires) == 1
    validate_wire_spans(result.routing.wires, result.positions, maximum_span=7.0)
