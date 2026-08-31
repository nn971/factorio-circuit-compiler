import copy

import pytest

from factorio_circuit.blueprint.geometry_verify import (
    BlueprintAnchorExpectation,
    BlueprintRegion,
    BlueprintRigidComponentExpectation,
    BlueprintRigidMemberExpectation,
    BlueprintSeamExpectation,
    verify_blueprint_geometry,
)
from factorio_circuit.blueprint.verify import (
    BlueprintVerificationError,
    compiler_prototype_specs,
)


def _geometry_blueprint() -> dict[str, object]:
    return {
        "blueprint": {
            "item": "blueprint",
            "entities": [
                {
                    "entity_number": 1,
                    "name": "constant-combinator",
                    "position": {"x": 0.0, "y": 1.0},
                },
                {
                    "entity_number": 2,
                    "name": "arithmetic-combinator",
                    "position": {"x": 2.0, "y": 1.0},
                },
                {
                    "entity_number": 3,
                    "name": "constant-combinator",
                    "position": {"x": 4.0, "y": 1.0},
                },
                {
                    "entity_number": 4,
                    "name": "constant-combinator",
                    "position": {"x": 10.0, "y": 0.0},
                },
                {
                    "entity_number": 5,
                    "name": "constant-combinator",
                    "position": {"x": 12.0, "y": 0.0},
                },
                {
                    "entity_number": 6,
                    "name": "constant-combinator",
                    "position": {"x": 20.0, "y": 0.0},
                },
            ],
            "wires": [[1, 2, 2, 2], [2, 4, 3, 2]],
        }
    }


def _anchors() -> tuple[BlueprintAnchorExpectation, ...]:
    return (
        BlueprintAnchorExpectation(
            "west",
            1,
            2,
            (0.0, 1.0),
            prototype="constant-combinator",
        ),
        BlueprintAnchorExpectation(
            "east",
            3,
            2,
            (4.0, 1.0),
            prototype="constant-combinator",
        ),
    )


def _seams() -> tuple[BlueprintSeamExpectation, ...]:
    west, east = _anchors()
    boundary = BlueprintRegion(0.0, 0.0, 4.0, 4.0)
    return (
        BlueprintSeamExpectation("west-seam", boundary, (west,)),
        BlueprintSeamExpectation("east-seam", boundary, (east,)),
    )


def _components() -> tuple[BlueprintRigidComponentExpectation, ...]:
    return (
        BlueprintRigidComponentExpectation(
            "device",
            origin=(10.0, 0.0),
            members=(
                BlueprintRigidMemberExpectation(
                    4,
                    (0.0, 0.0),
                    prototype="constant-combinator",
                ),
                BlueprintRigidMemberExpectation(
                    5,
                    (2.0, 0.0),
                    prototype="constant-combinator",
                ),
            ),
            footprints=(BlueprintRegion(-0.5, -0.5, 4.5, 0.5),),
            keepouts=(BlueprintRegion(5.0, -0.5, 6.0, 0.5),),
            adapter_regions=(BlueprintRegion(7.0, -0.5, 8.0, 0.5),),
        ),
    )


def test_verifies_exact_anchors_seams_and_rigid_regions() -> None:
    report = verify_blueprint_geometry(
        _geometry_blueprint(),
        prototype_specs=compiler_prototype_specs(),
        anchors=_anchors(),
        seams=_seams(),
        components=_components(),
    )

    assert report.verified_anchors == ("west", "east")
    assert report.verified_seams == ("west-seam", "east-seam")
    assert report.verified_components == ("device",)


def test_moved_serialized_anchor_is_rejected() -> None:
    artifact = _geometry_blueprint()
    artifact["blueprint"]["entities"][0]["position"] = {"x": 0.0, "y": 2.0}

    with pytest.raises(BlueprintVerificationError, match="anchor 'west' moved"):
        verify_blueprint_geometry(
            artifact,
            prototype_specs=compiler_prototype_specs(),
            anchors=_anchors(),
        )


def test_anchor_connector_and_prototype_are_explicit_contracts() -> None:
    bad_connector = BlueprintAnchorExpectation("west", 1, 3, (0.0, 1.0))
    with pytest.raises(BlueprintVerificationError, match="connector 3 is not exposed"):
        verify_blueprint_geometry(
            _geometry_blueprint(),
            prototype_specs=compiler_prototype_specs(),
            anchors=(bad_connector,),
        )

    wrong_prototype = BlueprintAnchorExpectation(
        "west",
        1,
        2,
        (0.0, 1.0),
        prototype="decider-combinator",
    )
    with pytest.raises(BlueprintVerificationError, match="expected prototype 'decider-combinator'"):
        verify_blueprint_geometry(
            _geometry_blueprint(),
            prototype_specs=compiler_prototype_specs(),
            anchors=(wrong_prototype,),
        )


def test_seam_anchor_drift_is_rejected_from_serialized_position() -> None:
    artifact = _geometry_blueprint()
    artifact["blueprint"]["entities"][2]["position"] = {"x": 4.0, "y": 2.0}

    with pytest.raises(BlueprintVerificationError, match="anchor 'east' moved"):
        verify_blueprint_geometry(
            artifact,
            prototype_specs=compiler_prototype_specs(),
            seams=_seams(),
        )


def test_rigid_member_drift_is_rejected() -> None:
    artifact = _geometry_blueprint()
    artifact["blueprint"]["entities"][4]["position"] = {"x": 13.0, "y": 0.0}

    with pytest.raises(BlueprintVerificationError, match="member 5 moved"):
        verify_blueprint_geometry(
            artifact,
            prototype_specs=compiler_prototype_specs(),
            components=_components(),
        )


def test_external_entity_cannot_enter_owned_or_keepout_regions() -> None:
    artifact = _geometry_blueprint()
    artifact["blueprint"]["entities"][5]["position"] = {"x": 14.0, "y": 0.0}
    with pytest.raises(BlueprintVerificationError, match="owned/keepout geometry"):
        verify_blueprint_geometry(
            artifact,
            prototype_specs=compiler_prototype_specs(),
            components=_components(),
        )

    artifact = _geometry_blueprint()
    artifact["blueprint"]["entities"][5]["position"] = {"x": 15.5, "y": 0.0}
    with pytest.raises(BlueprintVerificationError, match="owned/keepout geometry"):
        verify_blueprint_geometry(
            artifact,
            prototype_specs=compiler_prototype_specs(),
            components=_components(),
        )


def test_adapter_region_must_remain_empty() -> None:
    artifact = _geometry_blueprint()
    artifact["blueprint"]["entities"][5]["position"] = {"x": 17.5, "y": 0.0}

    with pytest.raises(BlueprintVerificationError, match="reserved adapter region"):
        verify_blueprint_geometry(
            artifact,
            prototype_specs=compiler_prototype_specs(),
            components=_components(),
        )


def test_quarter_turn_pose_is_reconstructed_without_synthesis_state() -> None:
    artifact = {
        "blueprint": {
            "item": "blueprint",
            "entities": [
                {
                    "entity_number": 1,
                    "name": "constant-combinator",
                    "position": {"x": 10.0, "y": 10.0},
                },
                {
                    "entity_number": 2,
                    "name": "constant-combinator",
                    "position": {"x": 8.0, "y": 10.0},
                },
            ],
        }
    }
    component = BlueprintRigidComponentExpectation(
        "rotated",
        origin=(10.0, 10.0),
        quarter_turns=1,
        members=(
            BlueprintRigidMemberExpectation(1, (0.0, 0.0)),
            BlueprintRigidMemberExpectation(2, (0.0, 2.0)),
        ),
        footprints=(BlueprintRegion(-0.5, -0.5, 0.5, 2.5),),
    )

    report = verify_blueprint_geometry(
        artifact,
        prototype_specs=compiler_prototype_specs(),
        components=(component,),
    )
    assert report.verified_components == ("rotated",)


def test_rigid_component_members_cannot_be_claimed_twice() -> None:
    first = _components()[0]
    second = BlueprintRigidComponentExpectation(
        "other",
        origin=(10.0, 0.0),
        members=(BlueprintRigidMemberExpectation(4, (0.0, 0.0)),),
        footprints=(BlueprintRegion(-0.5, -0.5, 0.5, 0.5),),
    )

    with pytest.raises(ValueError, match="belongs to both rigid components"):
        verify_blueprint_geometry(
            _geometry_blueprint(),
            prototype_specs=compiler_prototype_specs(),
            components=(first, second),
        )


def test_geometry_verification_does_not_mutate_serialized_fixture() -> None:
    artifact = _geometry_blueprint()
    before = copy.deepcopy(artifact)

    verify_blueprint_geometry(
        artifact,
        prototype_specs=compiler_prototype_specs(),
        anchors=_anchors(),
        seams=_seams(),
        components=_components(),
    )

    assert artifact == before
