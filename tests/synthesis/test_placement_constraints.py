import pytest

from factorio_circuit.ir.abstract_physical import (
    AbstractPhysicalCircuit,
    ConstantCombinator,
    EntityPlacementConstraint,
    EntityPlacementMode,
    PhysicalAnchor,
)
from factorio_circuit.synthesis.placement import PlacementOptions
from factorio_circuit.synthesis.placement_constraints import resolve_placement_constraints


def _anchored_circuit() -> AbstractPhysicalCircuit:
    circuit = AbstractPhysicalCircuit(
        name="anchored",
        entities=[ConstantCombinator(id=1, annotation_only=True)],
        placement_constraints=[
            EntityPlacementConstraint(
                entity=1,
                mode=EntityPlacementMode.ANCHORED,
                anchor=PhysicalAnchor("sensor"),
            )
        ],
    )
    circuit.validate()
    return circuit


def test_symbolic_anchor_resolves_to_concrete_entity_anchor() -> None:
    resolved = resolve_placement_constraints(
        _anchored_circuit(),
        PlacementOptions(anchor_io=False),
        {"sensor": (12.0, -4.0)},
    )

    assert resolved.anchors == {1: (12.0, -4.0)}


def test_unresolved_symbolic_anchor_is_allowed_in_ir_but_rejected_by_final_placement() -> None:
    circuit = _anchored_circuit()
    assert circuit.placement_constraints[0].anchor == PhysicalAnchor("sensor")

    with pytest.raises(ValueError, match="physical anchor.*sensor"):
        resolve_placement_constraints(circuit, PlacementOptions(anchor_io=False), None)


def test_symbolic_and_explicit_entity_anchors_must_agree() -> None:
    options = PlacementOptions(
        anchors={1: (1.0, 2.0)},
        anchor_io=False,
    )

    with pytest.raises(ValueError, match="conflicting explicit and symbolic anchors"):
        resolve_placement_constraints(
            _anchored_circuit(),
            options,
            {"sensor": (8.0, 9.0)},
        )


def test_free_constraint_needs_no_deployment_anchor() -> None:
    circuit = AbstractPhysicalCircuit(
        name="free",
        entities=[ConstantCombinator(id=1, annotation_only=True)],
        placement_constraints=[EntityPlacementConstraint(entity=1)],
    )
    resolved = resolve_placement_constraints(
        circuit,
        PlacementOptions(anchor_io=False),
        None,
    )

    assert resolved.anchors == {}
