from __future__ import annotations

from examples.train_stop_device_probe import compile_train_stop_device_probe
from factorio_circuit.devices import TRAIN_STOPPED_SIGNAL, TRAINS_COUNT_SIGNAL
from factorio_circuit.ir.oracle import is_provider_input_port_name
from factorio_circuit.ir.physical import OpaqueSingleConnectorEntity, WireColor


def _opaque_by_description(result, description: str) -> OpaqueSingleConnectorEntity:
    return next(
        entity
        for entity in result.physical_circuit.entities
        if isinstance(entity, OpaqueSingleConnectorEntity)
        and entity.blueprint_fields.get("player_description") == description
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


def test_train_stop_probe_composes_command_input_and_status_oracle() -> None:
    result = compile_train_stop_device_probe()

    assert [port.name for port in result.abstract_physical.inputs] == ["commands"]
    assert all(port.name != "status" for port in result.abstract_physical.inputs)
    assert all(
        not is_provider_input_port_name(port.name) for port in result.abstract_physical.outputs
    )
    assert all(
        "provider-port-proxy" not in (entity.description or "")
        for entity in result.physical_circuit.entities
    )

    opaque = [
        entity
        for entity in result.physical_circuit.entities
        if isinstance(entity, OpaqueSingleConnectorEntity)
    ]
    assert len(opaque) == 3

    command_dock = _opaque_by_description(
        result,
        "TRAIN STOP PORT commands — INPUT Level vector; GREEN",
    )
    status_dock = _opaque_by_description(
        result,
        "TRAIN STOP PORT status — OUTPUT Level vector; RED",
    )
    train_stop = _opaque_by_description(result, "TRAIN STOP typed command/status interface")

    assert command_dock.prototype == "constant-combinator"
    assert status_dock.prototype == "constant-combinator"
    assert train_stop.prototype == "train-stop"
    assert train_stop.physical_half_extent == (0.5, 0.5)
    assert result.layout.positions[command_dock.id] == (0.5, 0.5)
    assert result.layout.positions[status_dock.id] == (0.5, 2.5)
    assert result.layout.positions[train_stop.id] == (3.5, 1.0)

    commands_net = _net_for_port(result, input_name="commands")
    status_net = _net_for_port(result, output_name="status")
    assert result.layout.assigned_net_colors[commands_net] is WireColor.GREEN
    assert result.layout.assigned_net_colors[status_net] is WireColor.RED

    status_abstract_net = next(net for net in result.abstract_physical.nets if net.id == status_net)
    assert TRAIN_STOPPED_SIGNAL in status_abstract_net.fixed_signals
    assert TRAINS_COUNT_SIGNAL in status_abstract_net.fixed_signals

    assert any(
        wire.color is WireColor.GREEN
        and (
            (wire.source_entity == command_dock.id and wire.source_connector_id == 2)
            or (wire.target_entity == command_dock.id and wire.target_connector_id == 2)
        )
        for wire in result.layout.wires
    )
    assert any(
        wire.color is WireColor.RED
        and (
            (wire.source_entity == status_dock.id and wire.source_connector_id == 1)
            or (wire.target_entity == status_dock.id and wire.target_connector_id == 1)
        )
        for wire in result.layout.wires
    )

    train_stop_json = next(
        entity
        for entity in result.blueprint_json["blueprint"]["entities"]
        if entity["entity_number"] == train_stop.id
    )
    assert train_stop_json["name"] == "train-stop"
    assert train_stop_json["station"] == "F4 Circuit Interface"
    assert train_stop_json["position"] == {"x": 3.5, "y": 1.0}
    assert train_stop_json["control_behavior"] == {
        "input_networks": {"red": False, "green": True},
        "output_networks": {"red": True, "green": False},
        "send_to_train": True,
        "read_from_train": True,
        "read_stopped_train": True,
        "train_stopped_signal": {"type": "virtual", "name": "signal-T"},
        "set_trains_limit": True,
        "trains_limit_signal": {"type": "virtual", "name": "signal-L"},
        "read_trains_count": True,
        "trains_count_signal": {"type": "virtual", "name": "signal-C"},
        "set_priority": True,
        "priority_signal": {"type": "virtual", "name": "signal-P"},
    }
    assert result.blueprint_string.startswith("0")
