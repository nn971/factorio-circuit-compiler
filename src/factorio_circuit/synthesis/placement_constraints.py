"""Resolve abstract physical placement constraints at deployment time."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from factorio_circuit.ir.abstract_physical import (
    AbstractPhysicalCircuit,
    EntityPlacementMode,
)
from factorio_circuit.synthesis.placement import PlacementOptions, Position


def resolve_placement_constraints(
    circuit: AbstractPhysicalCircuit,
    options: PlacementOptions,
    anchor_positions: Mapping[str, Position] | None,
) -> PlacementOptions:
    """Resolve symbolic abstract anchors into the placer's concrete entity anchors.

    Abstract lowering may retain unresolved symbolic sites. Final placement, however, must know the
    coordinate of every ``ANCHORED`` entity. Explicit numeric entity anchors in ``PlacementOptions``
    may coexist with symbolic anchors when they agree.
    """

    anchored = [
        constraint
        for constraint in circuit.placement_constraints
        if constraint.mode is EntityPlacementMode.ANCHORED
    ]
    if not anchored:
        return options

    bindings = dict(anchor_positions or {})
    required = {
        constraint.anchor.name
        for constraint in anchored
        if constraint.anchor is not None
    }
    missing = sorted(required - bindings.keys())
    if missing:
        raise ValueError(
            "final placement requires coordinates for physical anchor(s): "
            + ", ".join(repr(name) for name in missing)
        )

    entity_anchors = dict(options.anchors)
    for constraint in anchored:
        assert constraint.anchor is not None
        position = bindings[constraint.anchor.name]
        existing = entity_anchors.get(constraint.entity)
        if existing is not None and existing != position:
            raise ValueError(
                f"entity {constraint.entity} has conflicting explicit and symbolic anchors"
            )
        entity_anchors[constraint.entity] = position
    return replace(options, anchors=entity_anchors)
