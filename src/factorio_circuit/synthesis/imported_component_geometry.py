"""Bridge imported blueprint geometry into the authoritative rigid-component contract."""

from __future__ import annotations

from factorio_circuit.synthesis.blueprint_component import ImportedBlueprintLayout
from factorio_circuit.synthesis.component_geometry import (
    ComponentAccessPoint,
    ComponentRegion,
    RigidComponentConstraint,
    RigidComponentMember,
)
from factorio_circuit.synthesis.placement import Position


def imported_layout_as_rigid_component(
    imported: ImportedBlueprintLayout,
    name: str,
    *,
    origin: Position,
    footprints: tuple[ComponentRegion, ...],
    keepouts: tuple[ComponentRegion, ...] = (),
    adapter_regions: tuple[ComponentRegion, ...] = (),
    access_points: tuple[ComponentAccessPoint, ...] = (),
    allowed_origins: tuple[Position, ...] | None = None,
) -> RigidComponentConstraint:
    """Create a rigid component after checking the imported entities' true declared boxes.

    ``RigidComponentConstraint`` itself remains independent of any prototype database. This helper
    uses the explicit half-extents captured by ``import_blueprint_layout`` to establish the stronger
    initial invariant needed by opaque devices: every complete source collision box fits inside the
    owned footprint and no source object occupies a reserved adapter region. D2 translations retain
    all member offsets, so these local checks remain true at every legal translated origin.
    """

    if not footprints:
        raise ValueError("imported rigid component requires at least one footprint")
    half_extents = imported.entity_half_extents
    members: list[RigidComponentMember] = []
    for entity_id, position in sorted(imported.layout.positions.items()):
        local = (position[0] - origin[0], position[1] - origin[1])
        half = half_extents[entity_id]
        if not any(footprint.contains_box(local, half) for footprint in footprints):
            raise ValueError(
                f"imported entity {entity_id} does not fit completely inside the declared "
                "component footprint under its prototype geometry"
            )
        if any(region.overlaps_box(local, half) for region in adapter_regions):
            raise ValueError(
                f"imported entity {entity_id} overlaps a reserved adapter region under its "
                "prototype geometry"
            )
        members.append(RigidComponentMember(entity_id, local))

    return RigidComponentConstraint(
        name,
        origin=origin,
        members=tuple(members),
        footprints=footprints,
        keepouts=keepouts,
        adapter_regions=adapter_regions,
        access_points=access_points,
        allowed_origins=allowed_origins,
    )


__all__ = ["imported_layout_as_rigid_component"]
