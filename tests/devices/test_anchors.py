import pytest

from factorio_circuit.devices import (
    AnchorBinding,
    AnchoredBlueprint,
    AnchorSpec,
    AssemblerDevice,
    BoundAnchor,
    compose_anchored_blueprints,
    device_as_anchored_blueprint,
)
from factorio_circuit.devices.protocol import DevicePortDirection
from factorio_circuit.ir.physical import SignalId, WireColor
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality

IRON_PLATE = SignalId("item", "iron-plate")


def _vector_spec(name: str, direction: DevicePortDirection, wire: WireColor) -> AnchorSpec:
    return AnchorSpec(
        name,
        direction,
        PayloadShape.VECTOR,
        TemporalModality.LEVEL,
        wire,
    )


def _constant_source(name: str, position: tuple[float, float]) -> AnchoredBlueprint:
    return AnchoredBlueprint(
        {
            "item": "blueprint",
            "entities": [
                {
                    "entity_number": 1,
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
                                            "type": "item",
                                            "name": "iron-plate",
                                            "quality": "normal",
                                            "comparator": "=",
                                            "count": 2,
                                        }
                                    ],
                                }
                            ]
                        }
                    },
                }
            ],
            "wires": [],
        },
        (
            BoundAnchor(
                _vector_spec(name, DevicePortDirection.OUTPUT, WireColor.GREEN),
                1,
                2,
                position,
            ),
        ),
        "source",
    )


def test_dead_anchor_is_rejected_before_composition() -> None:
    with pytest.raises(ValueError, match="electrically dead"):
        AnchoredBlueprint(
            {
                "item": "blueprint",
                "entities": [
                    {
                        "entity_number": 1,
                        "name": "constant-combinator",
                        "position": {"x": 1.5, "y": 1.5},
                    }
                ],
                "wires": [],
            },
            (
                BoundAnchor(
                    _vector_spec("dead", DevicePortDirection.OUTPUT, WireColor.GREEN),
                    1,
                    2,
                    (1.5, 1.5),
                ),
            ),
            "dead-component",
        )


def test_assembler_ports_convert_to_first_class_anchors() -> None:
    device = AssemblerDevice().build()
    anchored = device_as_anchored_blueprint(device)
    assert {anchor.name for anchor in anchored.anchors} == {
        "recipe",
        "enable",
        "requester_demand",
        "ingredients",
        "requester_contents",
        "provider_contents",
        "working",
        "finished",
    }
    assert anchored.anchor("requester_demand").spec.wire is WireColor.GREEN
    assert anchored.anchor("ingredients").spec.wire is WireColor.RED


def test_exact_overlap_composition_merges_anchor_without_cross_wire() -> None:
    device = device_as_anchored_blueprint(AssemblerDevice().build())
    demand = device.anchor("requester_demand")
    source = _constant_source("demand_out", demand.position)

    before_entities = len(device.blueprint["entities"]) + len(source.blueprint["entities"])
    before_wires = len(device.blueprint["wires"]) + len(source.blueprint["wires"])
    result = compose_anchored_blueprints(
        device,
        source,
        bindings=(AnchorBinding("requester_demand", "demand_out"),),
    )

    assert len(result.blueprint["entities"]) == before_entities - 1
    # The command source had no wire: exact-overlap composition must not invent one.
    assert len(result.blueprint["wires"]) == before_wires

    shared_id = demand.entity_number
    shared = next(
        entity
        for entity in result.blueprint["entities"]
        if int(entity["entity_number"]) == shared_id
    )
    assert "control_behavior" in shared
    assert any(
        (wire[0] == shared_id and wire[1] == 2) or (wire[2] == shared_id and wire[3] == 2)
        for wire in result.blueprint["wires"]
    )


def test_anchor_binding_rejects_wrong_direction_or_color() -> None:
    device = device_as_anchored_blueprint(AssemblerDevice().build())
    demand = device.anchor("requester_demand")
    bad_direction = AnchoredBlueprint(
        {
            "item": "blueprint",
            "entities": [
                {
                    "entity_number": 1,
                    "name": "constant-combinator",
                    "position": {"x": demand.position[0], "y": demand.position[1]},
                },
                {
                    "entity_number": 2,
                    "name": "small-lamp",
                    "position": {"x": demand.position[0] + 1, "y": demand.position[1]},
                },
            ],
            "wires": [[1, 2, 2, 2]],
        },
        (
            BoundAnchor(
                _vector_spec("also_input", DevicePortDirection.INPUT, WireColor.GREEN),
                1,
                2,
                demand.position,
            ),
        ),
    )
    with pytest.raises(ValueError, match="same direction"):
        compose_anchored_blueprints(
            device,
            bad_direction,
            bindings=(("requester_demand", "also_input"),),
        )

    ingredients = device.anchor("ingredients")
    bad_color = AnchoredBlueprint(
        {
            "item": "blueprint",
            "entities": [
                {
                    "entity_number": 1,
                    "name": "constant-combinator",
                    "position": {"x": ingredients.position[0], "y": ingredients.position[1]},
                },
                {
                    "entity_number": 2,
                    "name": "small-lamp",
                    "position": {"x": ingredients.position[0] + 1, "y": ingredients.position[1]},
                },
            ],
            "wires": [[1, 2, 2, 2]],
        },
        (
            BoundAnchor(
                _vector_spec("wrong_color", DevicePortDirection.INPUT, WireColor.GREEN),
                1,
                2,
                ingredients.position,
            ),
        ),
    )
    with pytest.raises(ValueError, match="wire colors"):
        compose_anchored_blueprints(
            device,
            bad_color,
            bindings=(("ingredients", "wrong_color"),),
        )
