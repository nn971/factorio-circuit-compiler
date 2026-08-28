import pytest

from factorio_circuit.blueprint.opaque_layout_encode import layout_to_blueprint_json_with_opaque
from factorio_circuit.ir.physical import (
    Connector,
    OpaqueDualConnectorEntity,
    OpaqueSingleConnectorEntity,
    WireColor,
)
from factorio_circuit.synthesis.blueprint_component import (
    BlueprintConnectorShape,
    BlueprintEntityPhysicalSpec,
    import_blueprint_layout,
)


def _specs() -> dict[str, BlueprintEntityPhysicalSpec]:
    return {
        "constant-combinator": BlueprintEntityPhysicalSpec((0.5, 0.5)),
        "arithmetic-combinator": BlueprintEntityPhysicalSpec(
            (1.0, 0.5),
            BlueprintConnectorShape.INPUT_OUTPUT,
        ),
    }


def test_import_preserves_payload_geometry_and_connector_identity() -> None:
    blueprint = {
        "item": "blueprint",
        "label": "opaque import",
        "entities": [
            {
                "entity_number": 1,
                "name": "constant-combinator",
                "position": {"x": 0.5, "y": 0.5},
                "player_description": "source",
                "control_behavior": {"sections": {"sections": []}},
            },
            {
                "entity_number": 2,
                "name": "arithmetic-combinator",
                "position": {"x": 3.0, "y": 0.5},
                "direction": 4,
                "control_behavior": {"arithmetic_conditions": {"operation": "*"}},
            },
        ],
        "wires": [[1, 2, 2, 2], [2, 4, 1, 2]],
    }

    imported = import_blueprint_layout(blueprint, prototype_specs=_specs())

    first = imported.layout.circuit.entity_by_id(1)
    second = imported.layout.circuit.entity_by_id(2)
    assert isinstance(first, OpaqueSingleConnectorEntity)
    assert isinstance(second, OpaqueDualConnectorEntity)
    assert imported.entity_half_extents == {1: (0.5, 0.5), 2: (1.0, 0.5)}
    assert first.blueprint_fields["player_description"] == "source"
    assert second.blueprint_fields["direction"] == 4
    assert imported.layout.circuit.connections[0].color is WireColor.GREEN
    assert imported.layout.circuit.connections[0].source.connector is Connector.SINGLE
    assert imported.layout.circuit.connections[0].target.connector is Connector.INPUT
    assert imported.layout.circuit.connections[1].source.connector is Connector.OUTPUT

    serialized = layout_to_blueprint_json_with_opaque(imported.layout)["blueprint"]
    entities = {entity["entity_number"]: entity for entity in serialized["entities"]}
    assert entities[1]["control_behavior"] == blueprint["entities"][0]["control_behavior"]
    assert entities[2]["control_behavior"] == blueprint["entities"][1]["control_behavior"]
    assert entities[2]["direction"] == 4
    assert serialized["wires"] == [[1, 2, 2, 4], [1, 2, 2, 2]]


def test_import_requires_explicit_prototype_geometry() -> None:
    blueprint = {
        "entities": [
            {
                "entity_number": 1,
                "name": "modded-machine",
                "position": {"x": 0.0, "y": 0.0},
            }
        ]
    }

    with pytest.raises(ValueError, match="no explicit physical specification"):
        import_blueprint_layout(blueprint, prototype_specs={})


def test_import_rejects_overlap_using_declared_real_boxes() -> None:
    blueprint = {
        "entities": [
            {
                "entity_number": 1,
                "name": "large-machine",
                "position": {"x": 0.0, "y": 0.0},
            },
            {
                "entity_number": 2,
                "name": "constant-combinator",
                "position": {"x": 1.0, "y": 0.0},
            },
        ]
    }
    specs = {
        "large-machine": BlueprintEntityPhysicalSpec((1.5, 1.5)),
        "constant-combinator": BlueprintEntityPhysicalSpec((0.5, 0.5)),
    }

    with pytest.raises(ValueError, match="overlap"):
        import_blueprint_layout(blueprint, prototype_specs=specs)


def test_import_rejects_connector_shape_mismatch() -> None:
    blueprint = {
        "entities": [
            {
                "entity_number": 1,
                "name": "constant-combinator",
                "position": {"x": 0.0, "y": 0.0},
            },
            {
                "entity_number": 2,
                "name": "constant-combinator",
                "position": {"x": 3.0, "y": 0.0},
            },
        ],
        "wires": [[1, 3, 2, 1]],
    }

    with pytest.raises(ValueError, match="single-connector"):
        import_blueprint_layout(blueprint, prototype_specs=_specs())
