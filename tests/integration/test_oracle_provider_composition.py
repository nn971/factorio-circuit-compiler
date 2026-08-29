from __future__ import annotations

from dataclasses import dataclass

from factorio_circuit import (
    Circuit,
    OraclePhysicalContext,
    OraclePortDisposition,
    ProviderRigidComponentProduct,
    SignalId,
    lower_to_abstract_physical,
)
from factorio_circuit.devices.protocol import (
    BoundDevicePort,
    DeviceEndpoint,
    DevicePortDirection,
    DevicePortSpec,
    DeviceProtocol,
    ExternalDeviceBlueprint,
)
from factorio_circuit.ir.physical import (
    ConstantCombinator,
    OpaqueDualConnectorEntity,
    OpaqueSingleConnectorEntity,
    WireColor,
)
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality
from factorio_circuit.synthesis import (
    BlueprintConnectorShape,
    BlueprintEntityPhysicalSpec,
    ComponentRegion,
)
from factorio_circuit.synthesis.placement import PlacementOptions

_VECTOR_INPUT = DevicePortSpec(
    "candidates",
    DevicePortDirection.INPUT,
    PayloadShape.VECTOR,
    TemporalModality.LEVEL,
    WireColor.GREEN,
)
_VECTOR_OUTPUT = DevicePortSpec(
    "choice",
    DevicePortDirection.OUTPUT,
    PayloadShape.VECTOR,
    TemporalModality.LEVEL,
    WireColor.RED,
)
_VECTOR_DEVICE = ExternalDeviceBlueprint(
    DeviceProtocol("e2-vector-device", (_VECTOR_INPUT, _VECTOR_OUTPUT)),
    {
        "label": "E2 vector provider",
        "entities": [
            {
                "entity_number": 1,
                "name": "arithmetic-combinator",
                "position": {"x": 0.0, "y": 0.0},
                "direction": 4,
                "player_description": "E2 opaque vector body",
            }
        ],
        "wires": [],
    },
    (
        BoundDevicePort(
            _VECTOR_INPUT,
            DeviceEndpoint(1, 2, WireColor.GREEN, (0.0, 0.0)),
        ),
        BoundDevicePort(
            _VECTOR_OUTPUT,
            DeviceEndpoint(1, 3, WireColor.RED, (0.0, 0.0)),
        ),
    ),
)
_VECTOR_SPECS = {
    "arithmetic-combinator": BlueprintEntityPhysicalSpec(
        (1.0, 0.5),
        BlueprintConnectorShape.INPUT_OUTPUT,
    )
}


@dataclass(frozen=True, slots=True)
class _VectorRigidProvider:
    def materialize(self, context: OraclePhysicalContext) -> OraclePortDisposition:
        context.add_rigid_component(
            ProviderRigidComponentProduct(
                "vector-provider",
                _VECTOR_DEVICE,
                _VECTOR_SPECS,
                origin=(0.0, 0.0),
                footprints=(ComponentRegion(-2.0, -1.0, 2.0, 1.0),),
                internal_wire_span=9.0,
                port_bindings=(
                    context.component_input_binding(
                        "candidates",
                        _VECTOR_DEVICE,
                        "candidates",
                    ),
                    context.component_output_binding(_VECTOR_DEVICE, "choice"),
                ),
            )
        )
        return OraclePortDisposition.CONSUMED


def _vector_circuit() -> Circuit:
    circuit = Circuit("e2_vector_composition")
    x = circuit.input("x")
    candidates = circuit.signals("candidates")
    choice = circuit.oracle_signals("choice")
    circuit.bind_oracle_input(choice, "candidates", candidates)
    circuit.output("logic", x + 1)
    circuit.output("choice", choice)
    return circuit


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


def test_e2_compiles_rigid_vector_provider_before_final_routing() -> None:
    result = _vector_circuit().compile(
        optimize=False,
        placement=PlacementOptions(
            strategy="annealed",
            anchor_io=False,
            iterations=0,
            restarts=1,
        ),
        oracle_providers={"choice": _VectorRigidProvider()},
    )

    opaque = [
        entity
        for entity in result.physical_circuit.entities
        if isinstance(entity, OpaqueDualConnectorEntity)
    ]
    assert len(opaque) == 1
    device = opaque[0]
    assert result.layout.positions[device.id] == (0.0, 0.0)
    assert device.id > max(entity.id for entity in result.abstract_physical.entities)
    assert all(
        "provider-port-proxy" not in (entity.description or "")
        for entity in result.physical_circuit.entities
    )

    input_net = _net_for_port(result, input_name="candidates")
    output_net = _net_for_port(result, output_name="choice")
    colors = result.layout.assigned_net_colors
    assert colors[input_net] is WireColor.GREEN
    assert colors[output_net] is WireColor.RED

    incident = [
        wire
        for wire in result.layout.wires
        if wire.source_entity == device.id or wire.target_entity == device.id
    ]
    assert any(
        wire.color is WireColor.GREEN
        and (
            (wire.source_entity == device.id and wire.source_connector_id == 2)
            or (wire.target_entity == device.id and wire.target_connector_id == 2)
        )
        for wire in incident
    )
    assert any(
        wire.color is WireColor.RED
        and (
            (wire.source_entity == device.id and wire.source_connector_id == 3)
            or (wire.target_entity == device.id and wire.target_connector_id == 3)
        )
        for wire in incident
    )

    for entity in result.physical_circuit.entities:
        if entity.id == device.id:
            continue
        x, y = result.layout.positions[entity.id]
        if isinstance(entity, (OpaqueSingleConnectorEntity, OpaqueDualConnectorEntity)):
            half_x, half_y = entity.physical_half_extent
        elif isinstance(entity, ConstantCombinator):
            half_x, half_y = (0.5, 0.5)
        else:
            half_x, half_y = (1.0, 0.5)
        assert not (
            x + half_x > -2.0 + 1e-9
            and x - half_x < 2.0 - 1e-9
            and y + half_y > -1.0 + 1e-9
            and y - half_y < 1.0 - 1e-9
        )

    blueprint_entity = next(
        entity
        for entity in result.blueprint_json["blueprint"]["entities"]
        if entity["entity_number"] == device.id
    )
    assert blueprint_entity["name"] == "arithmetic-combinator"
    assert blueprint_entity["player_description"] == "E2 opaque vector body"


def test_e2_rigid_vector_provider_is_deterministic() -> None:
    options = PlacementOptions(
        strategy="annealed",
        anchor_io=False,
        iterations=0,
        restarts=1,
    )
    first = _vector_circuit().compile(
        optimize=False,
        placement=options,
        oracle_providers={"choice": _VectorRigidProvider()},
    )
    second = _vector_circuit().compile(
        optimize=False,
        placement=options,
        oracle_providers={"choice": _VectorRigidProvider()},
    )
    assert first.blueprint_string == second.blueprint_string


_FIXED = SignalId("virtual", "signal-C")
_SCALAR_OUTPUT = DevicePortSpec(
    "temperature",
    DevicePortDirection.OUTPUT,
    PayloadShape.SCALAR,
    TemporalModality.LEVEL,
    WireColor.RED,
    _FIXED,
)
_SCALAR_DEVICE = ExternalDeviceBlueprint(
    DeviceProtocol("e2-scalar-device", (_SCALAR_OUTPUT,)),
    {
        "entities": [
            {
                "entity_number": 1,
                "name": "constant-combinator",
                "position": {"x": 12.5, "y": 0.5},
                "player_description": "E2 opaque scalar body",
            }
        ],
        "wires": [],
    },
    (
        BoundDevicePort(
            _SCALAR_OUTPUT,
            DeviceEndpoint(1, 1, WireColor.RED, (12.5, 0.5)),
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class _ScalarRigidProvider:
    def materialize(self, context: OraclePhysicalContext) -> OraclePortDisposition:
        context.add_rigid_component(
            ProviderRigidComponentProduct(
                "scalar-provider",
                _SCALAR_DEVICE,
                {"constant-combinator": BlueprintEntityPhysicalSpec((0.5, 0.5))},
                origin=(12.0, 0.0),
                footprints=(ComponentRegion(0.0, 0.0, 1.0, 1.0),),
                internal_wire_span=9.0,
                port_bindings=(context.component_output_binding(_SCALAR_DEVICE, "temperature"),),
            )
        )
        return OraclePortDisposition.CONSUMED


def test_e2_scalar_device_port_fixes_concrete_signal_identity() -> None:
    circuit = Circuit("e2_scalar_signal")
    temperature = circuit.oracle("temperature")
    circuit.output("temperature", temperature + 1)
    provider = _ScalarRigidProvider()

    lowered = lower_to_abstract_physical(
        circuit,
        optimize=False,
        oracle_providers={"temperature": provider},
    )
    binding = lowered.provider_materialization.rigid_components[0].port_bindings[0]
    provider_net = lowered.abstract_physical.net_by_id(binding.net_id)
    assert len(provider_net.signals) == 1

    result = circuit.compile(
        optimize=False,
        placement=PlacementOptions(
            strategy="annealed",
            anchor_io=False,
            iterations=0,
            restarts=1,
        ),
        oracle_providers={"temperature": provider},
    )

    assert result.layout.allocated_signals[provider_net.signals[0]] == _FIXED
    opaque = [
        entity
        for entity in result.physical_circuit.entities
        if isinstance(entity, OpaqueSingleConnectorEntity)
    ]
    assert len(opaque) == 1
    assert any(
        wire.color is WireColor.RED
        and (
            (wire.source_entity == opaque[0].id and wire.source_connector_id == 1)
            or (wire.target_entity == opaque[0].id and wire.target_connector_id == 1)
        )
        for wire in result.layout.wires
    )
