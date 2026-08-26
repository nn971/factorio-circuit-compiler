from __future__ import annotations

from copy import deepcopy

import pytest

from factorio_circuit.devices.anchors import AnchoredBlueprint, AnchorSpec, BoundAnchor
from factorio_circuit.devices.component_seams import (
    BoundarySlot,
    ComponentFootprint,
    ComponentSeam,
    ComponentSide,
    ConstrainedComponent,
    boundary_anchor,
    compose_component_seams,
)
from factorio_circuit.devices.protocol import DevicePortDirection
from factorio_circuit.ir.physical import WireColor
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality


def _vector_spec(name: str, direction: DevicePortDirection) -> AnchorSpec:
    return AnchorSpec(
        name,
        direction,
        PayloadShape.VECTOR,
        TemporalModality.LEVEL,
        WireColor.GREEN,
    )


def _cell(label: str = "cell") -> ConstrainedComponent:
    footprint = ComponentFootprint(0.0, 0.0, 4.0, 4.0)
    entities = [
        {
            "entity_number": 1,
            "name": "constant-combinator",
            "position": {"x": 0.0, "y": 1.0},
        },
        {
            "entity_number": 2,
            "name": "arithmetic-combinator",
            "position": {"x": 2.0, "y": 1.0},
            "direction": 4,
        },
        {
            "entity_number": 3,
            "name": "constant-combinator",
            "position": {"x": 4.0, "y": 1.0},
        },
        {
            "entity_number": 4,
            "name": "constant-combinator",
            "position": {"x": 0.0, "y": 3.0},
        },
        {
            "entity_number": 5,
            "name": "arithmetic-combinator",
            "position": {"x": 2.0, "y": 3.0},
            "direction": 4,
        },
        {
            "entity_number": 6,
            "name": "constant-combinator",
            "position": {"x": 4.0, "y": 3.0},
        },
    ]
    anchored = AnchoredBlueprint(
        {
            "item": "blueprint",
            "entities": entities,
            "wires": [
                [1, 2, 2, 2],
                [2, 4, 3, 2],
                [4, 2, 5, 2],
                [5, 4, 6, 2],
            ],
        },
        (
            boundary_anchor(
                _vector_spec("west_a", DevicePortDirection.INPUT),
                1,
                2,
                footprint,
                side=ComponentSide.WEST,
                slot=1,
            ),
            boundary_anchor(
                _vector_spec("east_a", DevicePortDirection.OUTPUT),
                3,
                2,
                footprint,
                side=ComponentSide.EAST,
                slot=1,
            ),
            boundary_anchor(
                _vector_spec("west_b", DevicePortDirection.INPUT),
                4,
                2,
                footprint,
                side=ComponentSide.WEST,
                slot=3,
            ),
            boundary_anchor(
                _vector_spec("east_b", DevicePortDirection.OUTPUT),
                6,
                2,
                footprint,
                side=ComponentSide.EAST,
                slot=3,
            ),
        ),
        label,
    )
    return ConstrainedComponent.bounded(
        anchored,
        footprint,
        slots=(
            BoundarySlot("west_a", ComponentSide.WEST, 1),
            BoundarySlot("west_b", ComponentSide.WEST, 3),
            BoundarySlot("east_a", ComponentSide.EAST, 1),
            BoundarySlot("east_b", ComponentSide.EAST, 3),
        ),
        seams=(
            ComponentSeam("west", ComponentSide.WEST, ("west_a", "west_b")),
            ComponentSeam("east", ComponentSide.EAST, ("east_a", "east_b")),
        ),
    )


def test_boundary_anchor_position_is_derived_from_side_and_slot() -> None:
    footprint = ComponentFootprint(10.0, 20.0, 18.0, 28.0, slot_pitch=2.0)
    anchor = boundary_anchor(
        _vector_spec("dock", DevicePortDirection.INPUT),
        1,
        2,
        footprint,
        side=ComponentSide.WEST,
        slot=3,
    )
    assert anchor.position == (10.0, 26.0)


def test_constrained_component_rejects_floating_boundary_anchor() -> None:
    cell = _cell()
    blueprint = deepcopy(cell.anchored.blueprint)
    moved = next(entity for entity in blueprint["entities"] if entity["entity_number"] == 1)
    moved["position"] = {"x": 0.0, "y": 2.0}
    anchors = tuple(
        BoundAnchor(
            anchor.spec,
            anchor.entity_number,
            anchor.connector_id,
            (0.0, 2.0) if anchor.name == "west_a" else anchor.position,
        )
        for anchor in cell.anchored.anchors
    )
    bad = AnchoredBlueprint(blueprint, anchors, "bad")

    with pytest.raises(ValueError, match="declared west slot"):
        ConstrainedComponent.bounded(
            bad,
            cell.footprints[0],
            slots=cell.slots,
            seams=cell.seams,
        )


def test_constrained_component_rejects_entity_outside_footprint() -> None:
    cell = _cell()
    blueprint = deepcopy(cell.anchored.blueprint)
    blueprint["entities"].append(
        {"entity_number": 99, "name": "small-lamp", "position": {"x": 9.0, "y": 9.0}}
    )
    anchored = AnchoredBlueprint(blueprint, cell.anchored.anchors, "escaped")
    with pytest.raises(ValueError, match="outside every component footprint"):
        ConstrainedComponent.bounded(
            anchored,
            cell.footprints[0],
            slots=cell.slots,
            seams=cell.seams,
        )


def test_seam_composition_derives_translation_and_preserves_regular_cells() -> None:
    first = _cell("first")
    second = _cell("second")
    third = _cell("third")

    pair = compose_component_seams(
        first,
        second,
        left_seam="east",
        right_seam="west",
        label="pair",
    )
    triple = compose_component_seams(
        pair,
        third,
        left_seam="east",
        right_seam="west",
        label="triple",
    )

    assert triple.footprints == (
        ComponentFootprint(0.0, 0.0, 4.0, 4.0),
        ComponentFootprint(4.0, 0.0, 8.0, 4.0),
        ComponentFootprint(8.0, 0.0, 12.0, 4.0),
    )
    assert {seam.name for seam in triple.seams} == {"west", "east"}
    assert triple.anchored.anchor("west_a").position == (0.0, 1.0)
    assert triple.anchored.anchor("east_a").position == (12.0, 1.0)

    # Two two-lane seam merges remove four terminal entities total; composition adds no routing
    # entities or wires of its own.
    assert len(triple.anchored.blueprint["entities"]) == 3 * 6 - 2 * 2
    assert len(triple.anchored.blueprint["wires"]) == 3 * 4


def test_seam_composition_rejects_non_rigid_lane_alignment() -> None:
    left = _cell("left")
    right = _cell("right")
    blueprint = deepcopy(right.anchored.blueprint)
    moved = next(entity for entity in blueprint["entities"] if entity["entity_number"] == 4)
    moved["position"] = {"x": 0.0, "y": 2.0}
    anchors = tuple(
        BoundAnchor(
            anchor.spec,
            anchor.entity_number,
            anchor.connector_id,
            (0.0, 2.0) if anchor.name == "west_b" else anchor.position,
        )
        for anchor in right.anchored.anchors
    )
    crooked_anchored = AnchoredBlueprint(blueprint, anchors, "crooked")
    crooked = ConstrainedComponent.bounded(
        crooked_anchored,
        right.footprints[0],
        slots=(
            BoundarySlot("west_a", ComponentSide.WEST, 1),
            BoundarySlot("west_b", ComponentSide.WEST, 2),
            BoundarySlot("east_a", ComponentSide.EAST, 1),
            BoundarySlot("east_b", ComponentSide.EAST, 3),
        ),
        seams=(
            ComponentSeam("west", ComponentSide.WEST, ("west_a", "west_b")),
            ComponentSeam("east", ComponentSide.EAST, ("east_a", "east_b")),
        ),
    )

    with pytest.raises(ValueError, match="one rigid component translation"):
        compose_component_seams(
            left,
            crooked,
            left_seam="east",
            right_seam="west",
        )


def test_seam_composition_requires_opposite_sides() -> None:
    left = _cell("left")
    right = _cell("right")
    with pytest.raises(ValueError, match="opposite directions"):
        compose_component_seams(
            left,
            right,
            left_seam="west",
            right_seam="west",
        )
