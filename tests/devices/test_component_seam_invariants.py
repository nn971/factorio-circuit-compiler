from __future__ import annotations

import pytest

from factorio_circuit.devices.anchors import AnchoredBlueprint, AnchorSpec, BoundAnchor
from factorio_circuit.devices.component_seams import (
    BoundarySlot,
    ComponentFootprint,
    ComponentSeam,
    ComponentSide,
    ConstrainedComponent,
)
from factorio_circuit.devices.protocol import DevicePortDirection
from factorio_circuit.ir.physical import WireColor
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality


def _source_spec(name: str) -> AnchorSpec:
    return AnchorSpec(
        name,
        DevicePortDirection.OUTPUT,
        PayloadShape.VECTOR,
        TemporalModality.LEVEL,
        WireColor.GREEN,
    )


def _source_entity(entity_number: int, position: tuple[float, float]) -> dict[str, object]:
    return {
        "entity_number": entity_number,
        "name": "constant-combinator",
        "position": {"x": position[0], "y": position[1]},
        "control_behavior": {
            "sections": {
                "sections": [
                    {
                        "index": 1,
                        "filters": [
                            {
                                "index": 1,
                                "type": "virtual",
                                "name": "signal-A",
                                "quality": "normal",
                                "comparator": "=",
                                "count": 1,
                            }
                        ],
                    }
                ]
            }
        },
    }


def test_boundary_slots_cannot_share_one_physical_dock() -> None:
    footprint = ComponentFootprint(0.0, 0.0, 4.0, 4.0)
    position = (0.0, 0.0)
    anchored = AnchoredBlueprint(
        {
            "item": "blueprint",
            "entities": [_source_entity(1, position), _source_entity(2, position)],
            "wires": [],
        },
        (
            BoundAnchor(_source_spec("west"), 1, 2, position),
            BoundAnchor(_source_spec("north"), 2, 2, position),
        ),
        "corner-collision",
    )

    with pytest.raises(ValueError, match="same dock coordinate"):
        ConstrainedComponent.bounded(
            anchored,
            footprint,
            slots=(
                BoundarySlot("west", ComponentSide.WEST, 0),
                BoundarySlot("north", ComponentSide.NORTH, 0),
            ),
            seams=(
                ComponentSeam("west-seam", ComponentSide.WEST, ("west",)),
                ComponentSeam("north-seam", ComponentSide.NORTH, ("north",)),
            ),
        )


def test_one_seam_cannot_span_multiple_component_footprints() -> None:
    left = ComponentFootprint(0.0, 0.0, 4.0, 4.0)
    right = ComponentFootprint(4.0, 0.0, 8.0, 4.0)
    left_position = left.boundary_position(ComponentSide.NORTH, 1)
    right_position = right.boundary_position(ComponentSide.NORTH, 1)
    anchored = AnchoredBlueprint(
        {
            "item": "blueprint",
            "entities": [
                _source_entity(1, left_position),
                _source_entity(2, right_position),
            ],
            "wires": [],
        },
        (
            BoundAnchor(_source_spec("left_lane"), 1, 2, left_position),
            BoundAnchor(_source_spec("right_lane"), 2, 2, right_position),
        ),
        "cross-region-seam",
    )

    with pytest.raises(ValueError, match="cannot span multiple component footprints"):
        ConstrainedComponent(
            anchored,
            (left, right),
            (
                BoundarySlot("left_lane", ComponentSide.NORTH, 1, 0),
                BoundarySlot("right_lane", ComponentSide.NORTH, 1, 1),
            ),
            (
                ComponentSeam(
                    "bad-seam",
                    ComponentSide.NORTH,
                    ("left_lane", "right_lane"),
                ),
            ),
        )
