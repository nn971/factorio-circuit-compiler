"""Typed physical protocols for reusable external Factorio devices.

A device protocol names logical ports before they are bound to one concrete blueprint.  The logical
metadata reuses the compiler's canonical payload-shape and Level/Event vocabulary, while
the endpoint records the concrete Factorio wire/color/connector a consumer should attach to.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from factorio_circuit.devices._blueprint import Blueprint, encode_blueprint
from factorio_circuit.ir.physical import SignalId, WireColor
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality


class DevicePortDirection(StrEnum):
    """Direction of information flow at an external-device boundary."""

    INPUT = "input"
    OUTPUT = "output"


class DeviceSide(StrEnum):
    """Named physical side used by mechanical attachment metadata."""

    NORTH = "north"
    EAST = "east"
    SOUTH = "south"
    WEST = "west"


@dataclass(frozen=True, slots=True)
class DevicePortSpec:
    """Logical and electrical contract for one named external-device port."""

    name: str
    direction: DevicePortDirection
    payload_shape: PayloadShape
    modality: TemporalModality
    wire: WireColor
    signal: SignalId | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("device port name must be non-empty")
        if self.payload_shape is PayloadShape.SCALAR and self.signal is None:
            raise ValueError(f"scalar device port {self.name!r} requires a fixed signal")
        if self.payload_shape is PayloadShape.VECTOR and self.signal is not None:
            raise ValueError(f"vector device port {self.name!r} cannot reserve one fixed signal")


@dataclass(frozen=True, slots=True)
class DeviceProtocol:
    """Reusable logical/electrical protocol independent of one blueprint placement."""

    name: str
    ports: tuple[DevicePortSpec, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("device protocol name must be non-empty")
        names = [port.name for port in self.ports]
        if len(set(names)) != len(names):
            raise ValueError(f"device protocol {self.name!r} contains duplicate port names")

    def port(self, name: str) -> DevicePortSpec:
        for port in self.ports:
            if port.name == name:
                return port
        raise KeyError(name)


@dataclass(frozen=True, slots=True)
class DeviceEndpoint:
    """Concrete blueprint endpoint implementing one protocol port."""

    entity_number: int
    connector_id: int
    wire: WireColor
    position: tuple[float, float]

    def __post_init__(self) -> None:
        if self.entity_number < 1:
            raise ValueError("device endpoint entity_number must be positive")
        if self.connector_id < 1:
            raise ValueError("device endpoint connector_id must be positive")


@dataclass(frozen=True, slots=True)
class BoundDevicePort:
    """One protocol port bound to a concrete blueprint endpoint."""

    spec: DevicePortSpec
    endpoint: DeviceEndpoint

    def __post_init__(self) -> None:
        if self.spec.wire is not self.endpoint.wire:
            raise ValueError(
                f"device port {self.spec.name!r} requires {self.spec.wire.value} wire, "
                f"got {self.endpoint.wire.value}"
            )

    @property
    def name(self) -> str:
        return self.spec.name


@dataclass(frozen=True, slots=True)
class DeviceAttachment:
    """Named mechanical attachment point relative to the generated device blueprint."""

    name: str
    side: DeviceSide
    position: tuple[float, float]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("device attachment name must be non-empty")


@dataclass(frozen=True, slots=True)
class ExternalDeviceBlueprint:
    """A materialized external device plus its typed protocol/geometry metadata."""

    protocol: DeviceProtocol
    blueprint: Blueprint
    ports: tuple[BoundDevicePort, ...]
    attachments: tuple[DeviceAttachment, ...] = ()

    def __post_init__(self) -> None:
        expected = {port.name for port in self.protocol.ports}
        actual = {port.name for port in self.ports}
        if actual != expected or len(self.ports) != len(self.protocol.ports):
            raise ValueError(
                f"bound device ports {sorted(actual)!r} do not match protocol "
                f"{sorted(expected)!r}"
            )
        attachment_names = [attachment.name for attachment in self.attachments]
        if len(set(attachment_names)) != len(attachment_names):
            raise ValueError("external device contains duplicate attachment names")

    def port(self, name: str) -> BoundDevicePort:
        for port in self.ports:
            if port.name == name:
                return port
        raise KeyError(name)

    def attachment(self, name: str) -> DeviceAttachment:
        for attachment in self.attachments:
            if attachment.name == name:
                return attachment
        raise KeyError(name)

    def blueprint_string(self) -> str:
        return encode_blueprint(self.blueprint)

    def anchored(self):
        """Return the generic exact-overlap anchoring view of this device."""

        from factorio_circuit.devices.anchors import device_as_anchored_blueprint

        return device_as_anchored_blueprint(self, label=self.protocol.name)
