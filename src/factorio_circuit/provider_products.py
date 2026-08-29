"""Typed physical products emitted by oracle providers before final placement.

E1 deliberately stops short of composing rigid products into the final compiler layout. The
important boundary is that providers can now describe reusable physical components explicitly,
using the same device/geometry vocabulary established by Milestone D, instead of relying on
post-synthesis edits. E2 consumes these declarations during unified physical composition.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import hypot
from types import MappingProxyType
from typing import TYPE_CHECKING

from factorio_circuit.synthesis.blueprint_component import (
    BlueprintEntityPhysicalSpec,
    import_blueprint_layout,
)
from factorio_circuit.synthesis.component_geometry import ComponentAccessPoint, ComponentRegion
from factorio_circuit.synthesis.imported_component_geometry import (
    imported_layout_as_rigid_component,
)
from factorio_circuit.synthesis.placement import Position

if TYPE_CHECKING:
    from factorio_circuit.devices.protocol import ExternalDeviceBlueprint

_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class ProviderComponentPortBinding:
    """Bind one reusable-device port to an abstract physical net.

    The binding is intentionally expressed in net ids rather than concrete wire colors or signal
    identities. Those remain late physical decisions for the unified E2 composition pass.
    """

    port_name: str
    net_id: int

    def __post_init__(self) -> None:
        if not self.port_name:
            raise ValueError("provider component port binding requires a non-empty port name")
        if isinstance(self.net_id, bool) or not isinstance(self.net_id, int) or self.net_id <= 0:
            raise ValueError("provider component port binding net_id must be a positive integer")


@dataclass(frozen=True, slots=True)
class ProviderRigidComponentProduct:
    """One reusable rigid device contributed by an oracle provider.

    Entity numbers inside ``device`` remain component-local at E1. E2 is responsible for rebasing
    them into the compiler-wide physical id space, connecting ``port_bindings``, and running D1-D3
    placement/routing. The declaration nevertheless validates its source blueprint and complete
    prototype-aware rigid geometry immediately, so an invalid component cannot cross the provider
    boundary.
    """

    name: str
    device: ExternalDeviceBlueprint
    prototype_specs: Mapping[str, BlueprintEntityPhysicalSpec]
    origin: Position
    footprints: tuple[ComponentRegion, ...]
    internal_wire_span: float
    port_bindings: tuple[ProviderComponentPortBinding, ...]
    keepouts: tuple[ComponentRegion, ...] = ()
    adapter_regions: tuple[ComponentRegion, ...] = ()
    access_points: tuple[ComponentAccessPoint, ...] = ()
    allowed_origins: tuple[Position, ...] | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("provider rigid component name must be non-empty")
        if self.internal_wire_span <= 0.0:
            raise ValueError("provider rigid component internal_wire_span must be positive")
        if not self.port_bindings:
            raise ValueError("provider rigid component requires at least one bound device port")
        names = [binding.port_name for binding in self.port_bindings]
        if len(set(names)) != len(names):
            raise ValueError("provider rigid component contains duplicate bound device ports")
        for binding in self.port_bindings:
            try:
                self.device.port(binding.port_name)
            except KeyError as exc:
                raise ValueError(
                    f"provider rigid component binds unknown device port {binding.port_name!r}"
                ) from exc

        # Detach caller-owned mutable state and establish the full D4 import/geometry invariant at
        # declaration time. E2 may safely rebase ids and translate this validated body.
        specs = MappingProxyType(dict(self.prototype_specs))
        object.__setattr__(self, "prototype_specs", specs)
        imported = import_blueprint_layout(
            self.device.blueprint,
            prototype_specs=specs,
            name=self.name,
        )
        for wire in imported.layout.wires:
            left = imported.layout.positions[wire.source_entity]
            right = imported.layout.positions[wire.target_entity]
            distance = hypot(left[0] - right[0], left[1] - right[1])
            if distance > self.internal_wire_span + _EPSILON:
                raise ValueError(
                    f"provider rigid component wire {wire.source_entity}->{wire.target_entity} "
                    f"spans {distance:.3f}, exceeding internal_wire_span "
                    f"{self.internal_wire_span:.3f}"
                )
        imported_layout_as_rigid_component(
            imported,
            self.name,
            origin=self.origin,
            footprints=self.footprints,
            keepouts=self.keepouts,
            adapter_regions=self.adapter_regions,
            access_points=self.access_points,
            allowed_origins=self.allowed_origins,
        )


__all__ = [
    "ProviderComponentPortBinding",
    "ProviderRigidComponentProduct",
]
