from __future__ import annotations

from examples.roboport_stock_reader_probe import compile_roboport_stock_reader_probe
from factorio_circuit.ir.physical import OpaqueSingleConnectorEntity, WireColor


def _opaque_by_description(result, description: str) -> OpaqueSingleConnectorEntity:
    return next(
        entity
        for entity in result.physical_circuit.entities
        if isinstance(entity, OpaqueSingleConnectorEntity)
        and entity.blueprint_fields.get("player_description") == description
    )


def _output_net(result, name: str) -> int:
    endpoint = next(port.endpoint for port in result.abstract_physical.outputs if port.name == name)
    return next(net.id for net in result.abstract_physical.nets if endpoint in net.endpoints)


def test_roboport_stock_reader_realizes_vector_oracle_as_rigid_component() -> None:
    result = compile_roboport_stock_reader_probe()

    opaque = [
        entity
        for entity in result.physical_circuit.entities
        if isinstance(entity, OpaqueSingleConnectorEntity)
    ]
    assert len(opaque) == 2
    assert all(
        "provider-port-proxy" not in (entity.description or "")
        for entity in result.physical_circuit.entities
    )

    dock = _opaque_by_description(result, "ROBOPORT PORT stock — OUTPUT Level vector; RED")
    roboport = _opaque_by_description(result, "ROBOPORT logistic-network stock reader")
    assert dock.prototype == "constant-combinator"
    assert dock.physical_half_extent == (0.5, 0.5)
    assert roboport.prototype == "roboport"
    assert roboport.physical_half_extent == (2.0, 2.0)
    assert result.layout.positions[dock.id] == (4.5, 2.5)
    assert result.layout.positions[roboport.id] == (2.0, 2.0)

    stock_net = _output_net(result, "stock")
    assert result.layout.assigned_net_colors[stock_net] is WireColor.RED
    assert any(
        wire.color is WireColor.RED
        and (
            (wire.source_entity == dock.id and wire.source_connector_id == 1)
            or (wire.target_entity == dock.id and wire.target_connector_id == 1)
        )
        for wire in result.layout.wires
    )

    roboport_json = next(
        entity
        for entity in result.blueprint_json["blueprint"]["entities"]
        if entity["entity_number"] == roboport.id
    )
    assert roboport_json["name"] == "roboport"
    assert roboport_json["position"] == {"x": 2.0, "y": 2.0}
    assert roboport_json["control_behavior"] == {
        "output_networks": {"red": True, "green": False},
        "read_items_mode": 1,
        "read_robot_stats": False,
    }
    assert result.blueprint_string.startswith("0")
