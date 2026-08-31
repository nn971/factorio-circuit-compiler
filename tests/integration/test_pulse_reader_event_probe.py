from __future__ import annotations

import pytest

from examples.pulse_reader_event_probe import (
    compile_inserter_pulse_reader_probe,
    compile_transport_belt_pulse_reader_probe,
)
from factorio_circuit import Circuit, OracleBindingError, ScalarConstantOracleProvider
from factorio_circuit.devices.pulse_readers import PULSE_VALID_SIGNAL
from factorio_circuit.ir.physical import (
    OpaqueDualConnectorEntity,
    OpaqueSingleConnectorEntity,
    WireColor,
)


def _opaque_by_description(result, description: str):
    return next(
        entity
        for entity in result.physical_circuit.entities
        if isinstance(entity, (OpaqueSingleConnectorEntity, OpaqueDualConnectorEntity))
        and entity.blueprint_fields.get("player_description") == description
    )


@pytest.mark.parametrize(
    ("compile_probe", "reader_description", "reader_prototype", "read_mode_key"),
    (
        (
            compile_transport_belt_pulse_reader_probe,
            "TRANSPORT BELT item pulse source",
            "transport-belt",
            "circuit_contents_read_mode",
        ),
        (
            compile_inserter_pulse_reader_probe,
            "INSERTER held-item pulse source",
            "inserter",
            "circuit_hand_read_mode",
        ),
    ),
)
def test_f3_rigid_pulse_reader_provides_vector_event_to_ordinary_state(
    compile_probe,
    reader_description: str,
    reader_prototype: str,
    read_mode_key: str,
) -> None:
    result = compile_probe()

    assert "items" not in {port.name for port in result.abstract_physical.inputs}
    assert "items__valid" not in {port.name for port in result.abstract_physical.inputs}
    opaque = [
        entity
        for entity in result.physical_circuit.entities
        if isinstance(entity, (OpaqueSingleConnectorEntity, OpaqueDualConnectorEntity))
    ]
    assert len(opaque) == 5
    assert all(
        "provider-port-proxy" not in (entity.description or "")
        for entity in result.physical_circuit.entities
    )
    assert any(
        "Event Accumulator total: add gated occurrence" in (entity.description or "")
        for entity in result.physical_circuit.entities
    )

    reader = _opaque_by_description(result, reader_description)
    assert reader.prototype == reader_prototype
    assert reader.blueprint_fields["control_behavior"] == {
        "output_networks": {"red": True, "green": True},
        "circuit_read_hand_contents": True,
        read_mode_key: 0,
    }

    payload_net = next(
        net
        for net in result.abstract_physical.nets
        if net.label == "event input items: vector payload"
    )
    valid_net = next(
        net for net in result.abstract_physical.nets if net.label == "event input items: valid"
    )
    assert result.layout.assigned_net_colors[payload_net.id] is WireColor.RED
    assert result.layout.assigned_net_colors[valid_net.id] is WireColor.GREEN
    assert len(valid_net.signals) == 1
    assert result.layout.allocated_signals[valid_net.signals[0]] == PULSE_VALID_SIGNAL

    payload_dock = _opaque_by_description(
        result,
        "PULSE READER PORT items — OUTPUT Event vector; RED",
    )
    valid_dock = _opaque_by_description(
        result,
        "PULSE READER PORT valid — OUTPUT Level scalar signal-V; GREEN",
    )
    payload_delay = _opaque_by_description(result, "PULSE READER align Event payload by one tick")
    valid_detector = _opaque_by_description(
        result,
        "PULSE READER derive aligned one-tick Event valid token",
    )
    assert any(
        wire.color is WireColor.RED and payload_dock.id in {wire.source_entity, wire.target_entity}
        for wire in result.layout.wires
    )
    assert any(
        wire.color is WireColor.GREEN and valid_dock.id in {wire.source_entity, wire.target_entity}
        for wire in result.layout.wires
    )
    assert payload_delay.prototype == "arithmetic-combinator"
    assert valid_detector.prototype == "decider-combinator"

    reader_json = next(
        entity
        for entity in result.blueprint_json["blueprint"]["entities"]
        if entity["entity_number"] == reader.id
    )
    assert reader_json["name"] == reader_prototype
    assert reader_json["control_behavior"][read_mode_key] == 0
    assert result.blueprint_string.startswith("0")


def test_event_oracle_requires_an_explicit_provider() -> None:
    circuit = Circuit("missing_event_provider")
    items = circuit.oracle_signal_event("items", guaranteed_min_separation=1)
    circuit.output("items", items)

    with pytest.raises(OracleBindingError, match="missing"):
        circuit.compile(optimize=False)


def test_level_provider_cannot_consume_only_half_of_event_boundary() -> None:
    circuit = Circuit("event_provider_pair_contract")
    alarm = circuit.oracle_event("alarm", guaranteed_min_separation=1)
    circuit.output("alarm", alarm)

    with pytest.raises(OracleBindingError, match="payload and valid together"):
        circuit.compile(
            optimize=False,
            oracle_providers={"alarm": ScalarConstantOracleProvider(1)},
        )


def test_ordinary_event_remains_an_external_payload_valid_boundary() -> None:
    circuit = Circuit("ordinary_external_event")
    items = circuit.signal_event("items", guaranteed_min_separation=1)
    circuit.output("items", items)

    result = circuit.compile(optimize=False)
    names = {port.name for port in result.abstract_physical.inputs}
    assert {"items", "items__valid"} <= names
