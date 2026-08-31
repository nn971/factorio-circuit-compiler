"""Physical provider binding for Event-bearing modules.

External Event lowering already exposes one payload input plus a one-tick ``<name>__valid`` input.
This module lets target-side oracle providers own that existing ABI instead of inventing a parallel
Event representation. Level oracles in the same clocked module continue to use the ordinary
:class:`OraclePhysicalContext`; Event oracles receive an :class:`EventOraclePhysicalContext` that
binds a typed Event payload port and a scalar Level-valid port to the two existing abstract nets.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from factorio_circuit.ir.abstract_physical import AbstractPhysicalCircuit, Endpoint, InputPort
from factorio_circuit.ir.oracle import (
    EventOracleInput,
    is_provider_input_port_name,
    oracle_sources,
)
from factorio_circuit.ir.semantic import CircuitModule, PayloadShape, TemporalModality
from factorio_circuit.oracles import (
    OracleBindingError,
    OraclePhysicalContext,
    OraclePortDisposition,
    OracleProvider,
    OracleProviderMaterialization,
    ProviderPhysicalProduct,
    _remove_annotation_marker,
)
from factorio_circuit.provider_products import (
    ProviderComponentPortBinding,
    ProviderRigidComponentProduct,
)

if TYPE_CHECKING:
    from factorio_circuit.devices.protocol import ExternalDeviceBlueprint


@dataclass(slots=True)
class EventOraclePhysicalContext(OraclePhysicalContext):
    """Provider context for one external Event payload plus its one-tick valid token."""

    valid_port: InputPort
    valid_net_id: int

    def __post_init__(self) -> None:
        if not isinstance(self.source, EventOracleInput):
            raise OracleBindingError("Event oracle context requires an EventOracleInput source")
        if self.valid_port.signal is None:
            raise OracleBindingError("Event oracle valid boundary requires one scalar signal")

    @property
    def is_vector(self) -> bool:
        assert isinstance(self.source, EventOracleInput)
        return self.source.payload_shape is PayloadShape.VECTOR

    @property
    def valid_signal(self) -> int:
        signal = self.valid_port.signal
        assert signal is not None
        return signal

    def attach(self, endpoint: Endpoint) -> None:
        """Reject payload-only attachment because an Event boundary is a synchronized pair."""

        del endpoint
        raise OracleBindingError(
            f"Event oracle {self.source.name!r} must realize payload and valid together; "
            "use an Event-aware provider binding"
        )

    def component_output_binding(
        self,
        device: ExternalDeviceBlueprint,
        port_name: str,
    ) -> ProviderComponentPortBinding:
        """Direct callers to the paired Event-device binding helper."""

        del device, port_name
        raise OracleBindingError(
            f"Event oracle {self.source.name!r} requires paired payload/valid bindings; "
            "use component_event_output_bindings()"
        )

    def component_event_output_bindings(
        self,
        device: ExternalDeviceBlueprint,
        payload_port_name: str,
        valid_port_name: str,
    ) -> tuple[ProviderComponentPortBinding, ProviderComponentPortBinding]:
        """Bind one typed device Event output onto the existing payload/valid physical ABI."""

        try:
            payload = device.port(payload_port_name)
        except KeyError as exc:
            raise OracleBindingError(f"device has no port {payload_port_name!r}") from exc
        try:
            valid = device.port(valid_port_name)
        except KeyError as exc:
            raise OracleBindingError(f"device has no port {valid_port_name!r}") from exc

        if payload.spec.direction.value != "output":
            raise OracleBindingError(
                f"device port {payload_port_name!r} must be an output to provide Event oracle "
                f"{self.source.name!r}"
            )
        if payload.spec.modality is not TemporalModality.EVENT:
            raise OracleBindingError(
                f"device port {payload_port_name!r} must be Event for Event oracle "
                f"{self.source.name!r}"
            )
        expected_shape = PayloadShape.VECTOR if self.is_vector else PayloadShape.SCALAR
        if payload.spec.payload_shape is not expected_shape:
            raise OracleBindingError(
                f"device Event port {payload_port_name!r} has {payload.spec.payload_shape.value} "
                f"payload; oracle {self.source.name!r} requires {expected_shape.value}"
            )

        if valid.spec.direction.value != "output":
            raise OracleBindingError(f"device valid port {valid_port_name!r} must be an output")
        if valid.spec.modality is not TemporalModality.LEVEL:
            raise OracleBindingError(f"device valid port {valid_port_name!r} must be Level")
        if valid.spec.payload_shape is not PayloadShape.SCALAR or valid.spec.signal is None:
            raise OracleBindingError(
                f"device valid port {valid_port_name!r} must be a fixed-signal scalar"
            )

        return (
            ProviderComponentPortBinding(payload_port_name, self.net_id),
            ProviderComponentPortBinding(valid_port_name, self.valid_net_id),
        )

    def add_rigid_component(
        self,
        product: ProviderRigidComponentProduct,
    ) -> ProviderRigidComponentProduct:
        """Require a rigid Event provider to bind both halves of the Event boundary."""

        if not isinstance(product, ProviderRigidComponentProduct):
            raise OracleBindingError("provider rigid component product has an invalid type")

        payload_bindings = [
            binding for binding in product.port_bindings if binding.net_id == self.net_id
        ]
        valid_bindings = [
            binding for binding in product.port_bindings if binding.net_id == self.valid_net_id
        ]
        if len(payload_bindings) != 1 or len(valid_bindings) != 1:
            raise OracleBindingError(
                f"Event provider component {product.name!r} must bind exactly one payload port and "
                "one valid port"
            )

        payload = product.device.port(payload_bindings[0].port_name)
        valid = product.device.port(valid_bindings[0].port_name)
        expected_shape = PayloadShape.VECTOR if self.is_vector else PayloadShape.SCALAR
        if (
            payload.spec.direction.value != "output"
            or payload.spec.modality is not TemporalModality.EVENT
            or payload.spec.payload_shape is not expected_shape
        ):
            raise OracleBindingError(
                f"Event provider component {product.name!r} payload binding must be an "
                f"OUTPUT {expected_shape.value} Event port"
            )
        if (
            valid.spec.direction.value != "output"
            or valid.spec.modality is not TemporalModality.LEVEL
            or valid.spec.payload_shape is not PayloadShape.SCALAR
            or valid.spec.signal is None
        ):
            raise OracleBindingError(
                f"Event provider component {product.name!r} valid binding must be an OUTPUT "
                "fixed-signal scalar Level port"
            )
        return OraclePhysicalContext.add_rigid_component(self, product)


def _unique_input_port(circuit: AbstractPhysicalCircuit, name: str) -> InputPort:
    matches = [port for port in circuit.inputs if port.name == name]
    if len(matches) != 1:
        raise OracleBindingError(f"physical lowering did not expose input {name!r} exactly once")
    return matches[0]


def _unique_port_net(circuit: AbstractPhysicalCircuit, port: InputPort) -> int:
    matches = [net.id for net in circuit.nets if port.endpoint in net.endpoints]
    if len(matches) != 1:
        raise OracleBindingError(f"physical input {port.name!r} must belong to exactly one net")
    return matches[0]


def materialize_clocked_oracle_providers(
    module: CircuitModule,
    circuit: AbstractPhysicalCircuit,
    providers: Mapping[str, OracleProvider],
) -> OracleProviderMaterialization:
    """Materialize Level and Event oracle providers after clocked physical lowering."""

    if not providers:
        return OracleProviderMaterialization()

    next_entity_id = max((entity.id for entity in circuit.entities), default=0) + 1
    consumed_provider_inputs: set[str] = set()
    products: list[ProviderPhysicalProduct] = []
    consumed_ports: list[InputPort] = []

    for source in oracle_sources(module):
        payload_port = _unique_input_port(circuit, source.name)
        payload_net_id = _unique_port_net(circuit, payload_port)
        valid_port: InputPort | None
        if isinstance(source, EventOracleInput):
            valid_port = _unique_input_port(circuit, f"{source.name}__valid")
            valid_net_id = _unique_port_net(circuit, valid_port)
            context: OraclePhysicalContext = EventOraclePhysicalContext(
                circuit=circuit,
                source=source,
                port=payload_port,
                net_id=payload_net_id,
                _next_entity_id=next_entity_id,
                _consumed_provider_inputs=consumed_provider_inputs,
                _products=[],
                valid_port=valid_port,
                valid_net_id=valid_net_id,
            )
        else:
            valid_port = None
            context = OraclePhysicalContext(
                circuit=circuit,
                source=source,
                port=payload_port,
                net_id=payload_net_id,
                _next_entity_id=next_entity_id,
                _consumed_provider_inputs=consumed_provider_inputs,
                _products=[],
            )

        disposition = providers[source.name].materialize(context)
        next_entity_id = context._next_entity_id
        if not isinstance(disposition, OraclePortDisposition):
            raise OracleBindingError(
                f"provider for oracle {source.name!r} returned an invalid port disposition"
            )
        products.extend(context.products)
        if disposition is OraclePortDisposition.CONSUMED:
            consumed_ports.append(payload_port)
            if valid_port is not None:
                consumed_ports.append(valid_port)

    hidden_ports = [port for port in circuit.outputs if is_provider_input_port_name(port.name)]
    unconsumed = sorted(
        port.name for port in hidden_ports if port.name not in consumed_provider_inputs
    )
    if unconsumed:
        raise OracleBindingError(
            "oracle provider input tap(s) were not consumed by their provider: "
            + ", ".join(repr(name) for name in unconsumed)
        )

    if consumed_ports:
        consumed_names = {port.name for port in consumed_ports}
        circuit.inputs = [port for port in circuit.inputs if port.name not in consumed_names]
        for consumed_port in consumed_ports:
            _remove_annotation_marker(circuit, consumed_port.endpoint.entity)

    if hidden_ports:
        hidden_names = {port.name for port in hidden_ports}
        circuit.outputs = [port for port in circuit.outputs if port.name not in hidden_names]
        for hidden_port in hidden_ports:
            _remove_annotation_marker(circuit, hidden_port.endpoint.entity)

    circuit.validate()
    return OracleProviderMaterialization(tuple(products))


__all__ = ["EventOraclePhysicalContext", "materialize_clocked_oracle_providers"]
