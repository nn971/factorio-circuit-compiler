from __future__ import annotations

import pytest

from examples.oracle_provider_mixed_probe import (
    ANCHORED_SENSOR_POSITION,
    compile_mixed_provider_probe,
)
from factorio_circuit.ir.physical import (
    ArithmeticCombinator,
    OpaqueDualConnectorEntity,
    OpaqueSingleConnectorEntity,
    WireColor,
)


def _net_for_port(result, *, input_name: str | None = None, output_name: str | None = None) -> int:
    if input_name is not None:
        endpoint = next(
            port.endpoint for port in result.abstract_physical.inputs if port.name == input_name
        )
    else:
        assert output_name is not None
        endpoint = next(
            port.endpoint for port in result.abstract_physical.outputs if port.name == output_name
        )
    return next(net.id for net in result.abstract_physical.nets if endpoint in net.endpoints)


def _ordinary_entity_by_description(result, description: str):
    return next(
        entity
        for entity in result.physical_circuit.entities
        if not isinstance(entity, (OpaqueSingleConnectorEntity, OpaqueDualConnectorEntity))
        and entity.description == description
    )


def _opaque_entity_by_description(result, description: str):
    return next(
        entity
        for entity in result.physical_circuit.entities
        if isinstance(entity, (OpaqueSingleConnectorEntity, OpaqueDualConnectorEntity))
        and entity.blueprint_fields.get("player_description") == description
    )


@pytest.mark.acceptance
def test_e3_compiles_ordinary_free_anchored_and_real_rigid_provider_together() -> None:
    result = compile_mixed_provider_probe()

    opaque = [
        entity
        for entity in result.physical_circuit.entities
        if isinstance(entity, (OpaqueSingleConnectorEntity, OpaqueDualConnectorEntity))
    ]
    assert len(opaque) == 25
    assert all(
        "provider-port-proxy" not in (entity.description or "")
        for entity in result.physical_circuit.entities
    )

    free_bias = _ordinary_entity_by_description(result, "ORACLE free_bias: constant 3")
    anchored_sensor = _ordinary_entity_by_description(
        result,
        "ORACLE anchored_sensor: constant 7",
    )
    assert result.layout.positions[anchored_sensor.id] == ANCHORED_SENSOR_POSITION
    assert result.layout.positions[free_bias.id] != ANCHORED_SENSOR_POSITION

    ordinary_arithmetic = [
        entity
        for entity in result.physical_circuit.entities
        if isinstance(entity, ArithmeticCombinator)
        and not isinstance(entity, OpaqueDualConnectorEntity)
    ]
    assert ordinary_arithmetic

    machine = _opaque_entity_by_description(result, "ASSEMBLER DEVICE machine")
    requester = _opaque_entity_by_description(result, "ASSEMBLER DEVICE requester chest")
    assert machine.prototype == "assembling-machine-3"
    assert machine.physical_half_extent == (1.5, 1.5)
    assert requester.prototype == "requester-chest"
    assert result.layout.positions[machine.id] == (8.5, 8.5)
    assert result.layout.positions[requester.id] == (8.5, 11.5)

    recipe_net = _net_for_port(result, input_name="recipe")
    ingredients_net = _net_for_port(result, output_name="ingredients")
    assert result.layout.assigned_net_colors[recipe_net] is WireColor.GREEN
    assert result.layout.assigned_net_colors[ingredients_net] is WireColor.RED

    recipe_dock = _opaque_entity_by_description(
        result,
        "ASSEMBLER PORT recipe — INPUT Level vector; GREEN",
    )
    ingredient_dock = _opaque_entity_by_description(
        result,
        "ASSEMBLER PORT ingredients — OUTPUT Level vector; RED",
    )
    recipe_incident = [
        wire
        for wire in result.layout.wires
        if wire.source_entity == recipe_dock.id or wire.target_entity == recipe_dock.id
    ]
    ingredient_incident = [
        wire
        for wire in result.layout.wires
        if wire.source_entity == ingredient_dock.id or wire.target_entity == ingredient_dock.id
    ]
    assert len(recipe_incident) >= 2
    assert len(ingredient_incident) >= 2
    assert any(
        wire.color is WireColor.GREEN
        and (
            (wire.source_entity == recipe_dock.id and wire.source_connector_id == 2)
            or (wire.target_entity == recipe_dock.id and wire.target_connector_id == 2)
        )
        for wire in recipe_incident
    )
    assert any(
        wire.color is WireColor.RED
        and (
            (wire.source_entity == ingredient_dock.id and wire.source_connector_id == 1)
            or (wire.target_entity == ingredient_dock.id and wire.target_connector_id == 1)
        )
        for wire in ingredient_incident
    )

    machine_json = next(
        entity
        for entity in result.blueprint_json["blueprint"]["entities"]
        if entity["entity_number"] == machine.id
    )
    assert machine_json["name"] == "assembling-machine-3"
    assert machine_json["position"] == {"x": 8.5, "y": 8.5}
    assert machine_json["control_behavior"]["set_recipe"] is True
    assert machine_json["control_behavior"]["read_ingredients"] is True

    anchored_json = next(
        entity
        for entity in result.blueprint_json["blueprint"]["entities"]
        if entity["entity_number"] == anchored_sensor.id
    )
    assert anchored_json["position"] == {
        "x": ANCHORED_SENSOR_POSITION[0],
        "y": ANCHORED_SENSOR_POSITION[1],
    }
    assert result.blueprint_string.startswith("0")
