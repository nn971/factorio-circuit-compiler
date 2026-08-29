from __future__ import annotations

from dataclasses import dataclass

import pytest

from factorio_circuit import (
    Circuit,
    FreePlacement,
    OracleBindingError,
    OraclePhysicalContext,
    OraclePortDisposition,
    ProviderComponentPortBinding,
    ProviderEntityProduct,
    ProviderRigidComponentProduct,
    ScalarConstantOracleProvider,
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
from factorio_circuit.ir.physical import WireColor
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality
from factorio_circuit.synthesis import (
    BlueprintConnectorShape,
    BlueprintEntityPhysicalSpec,
    ComponentAccessPoint,
    ComponentRegion,
)


_INPUT = DevicePortSpec(
    "candidates",
    DevicePortDirection.INPUT,
    PayloadShape.VECTOR,
    TemporalModality.LEVEL,
    WireColor.GREEN,
)
_OUTPUT = DevicePortSpec(
    "choice",
    DevicePortDirection.OUTPUT,
    PayloadShape.VECTOR,
    TemporalModality.LEVEL,
    WireColor.RED,
)
_DEVICE = ExternalDeviceBlueprint(
    DeviceProtocol("e1-rigid-provider-probe", (_INPUT, _OUTPUT)),
    {
        "label": "E1 rigid provider probe",
        "entities": [
            {
                "entity_number": 1,
                "name": "arithmetic-combinator",
                "position": {"x": 1.0, "y": 0.5},
            }
        ],
        "wires": [],
    },
    (
        BoundDevicePort(_INPUT, DeviceEndpoint(1, 2, WireColor.GREEN, (1.0, 0.5))),
        BoundDevicePort(_OUTPUT, DeviceEndpoint(1, 3, WireColor.RED, (1.0, 0.5))),
    ),
)
_SPECS = {
    "arithmetic-combinator": BlueprintEntityPhysicalSpec(
        (1.0, 0.5),
        BlueprintConnectorShape.INPUT_OUTPUT,
    )
}


@dataclass(frozen=True, slots=True)
class _RigidVectorProvider:
    consume_candidates: bool = False

    def materialize(self, context: OraclePhysicalContext) -> OraclePortDisposition:
        bindings = [context.component_output_binding(_DEVICE, "choice")]
        if self.consume_candidates:
            bindings.append(context.component_input_binding("candidates", _DEVICE, "candidates"))
        context.add_rigid_component(
            ProviderRigidComponentProduct(
                "e1-provider-component",
                _DEVICE,
                _SPECS,
                origin=(0.0, 0.0),
                footprints=(ComponentRegion(0.0, 0.0, 2.0, 1.0),),
                internal_wire_span=9.0,
                port_bindings=tuple(bindings),
                access_points=(
                    ComponentAccessPoint("west", (0.0, 0.5)),
                    ComponentAccessPoint("east", (2.0, 0.5)),
                ),
                allowed_origins=((0.0, 0.0), (10.0, 0.0)),
            )
        )
        return OraclePortDisposition.CONSUMED


def test_ordinary_provider_entities_are_reported_as_typed_products() -> None:
    circuit = Circuit("e1_entity_product")
    temperature = circuit.oracle("temperature")
    circuit.output("temperature", temperature + 1)

    lowered = lower_to_abstract_physical(
        circuit,
        optimize=False,
        oracle_providers={"temperature": ScalarConstantOracleProvider(42)},
    )

    assert len(lowered.provider_materialization.products) == 1
    product = lowered.provider_materialization.products[0]
    assert isinstance(product, ProviderEntityProduct)
    assert isinstance(product.placement, FreePlacement)
    assert product.entity_id in {entity.id for entity in lowered.abstract_physical.entities}
    assert lowered.provider_materialization.rigid_components == ()


def test_rigid_component_product_survives_abstract_lowering_without_posthoc_entities() -> None:
    circuit = Circuit("e1_rigid_product")
    choice = circuit.oracle_signals("choice")
    circuit.output("choice", choice)

    lowered = lower_to_abstract_physical(
        circuit,
        optimize=False,
        oracle_providers={"choice": _RigidVectorProvider()},
    )

    assert lowered.abstract_physical.inputs == []
    assert lowered.provider_materialization.entity_products == ()
    assert len(lowered.provider_materialization.rigid_components) == 1
    product = lowered.provider_materialization.rigid_components[0]
    assert product.name == "e1-provider-component"
    assert product.device is _DEVICE
    assert product.port_bindings[0].port_name == "choice"
    assert product.port_bindings[0].net_id in {net.id for net in lowered.abstract_physical.nets}


def test_rigid_component_can_bind_and_consume_named_provider_input_net() -> None:
    circuit = Circuit("e1_rigid_input")
    candidates = circuit.signals("candidates")
    choice = circuit.oracle_signals("choice")
    circuit.bind_oracle_input(choice, "candidates", candidates)
    circuit.output("choice", choice)

    lowered = lower_to_abstract_physical(
        circuit._build_for_physical(),
        optimize=False,
        oracle_providers={"choice": _RigidVectorProvider(consume_candidates=True)},
    )

    product = lowered.provider_materialization.rigid_components[0]
    by_port = {binding.port_name: binding.net_id for binding in product.port_bindings}
    assert set(by_port) == {"choice", "candidates"}
    assert by_port["choice"] != by_port["candidates"]
    # The hidden provider-input boundary is consumed even though its eventual endpoint lives in
    # the not-yet-composed rigid component.
    assert all("provider" not in port.name for port in lowered.abstract_physical.outputs)


def test_full_compile_refuses_to_silently_drop_e1_rigid_product() -> None:
    circuit = Circuit("e1_compile_guard")
    choice = circuit.oracle_signals("choice")
    circuit.output("choice", choice)

    with pytest.raises(OracleBindingError, match="E2 unified physical composition"):
        circuit.compile(
            optimize=False,
            oracle_providers={"choice": _RigidVectorProvider()},
        )


def test_rigid_product_validates_declared_device_geometry_immediately() -> None:
    product = ProviderRigidComponentProduct(
        "valid",
        _DEVICE,
        _SPECS,
        origin=(0.0, 0.0),
        footprints=(ComponentRegion(0.0, 0.0, 2.0, 1.0),),
        internal_wire_span=9.0,
        port_bindings=(ProviderComponentPortBinding("choice", 1),),
    )
    assert product.prototype_specs["arithmetic-combinator"].half_extent == (1.0, 0.5)

    with pytest.raises(ValueError, match="does not fit completely"):
        ProviderRigidComponentProduct(
            "too-small",
            _DEVICE,
            _SPECS,
            origin=(0.0, 0.0),
            footprints=(ComponentRegion(0.0, 0.0, 1.0, 1.0),),
            internal_wire_span=9.0,
            port_bindings=(ProviderComponentPortBinding("choice", 1),),
        )
