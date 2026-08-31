import pytest

from factorio_circuit.blueprint.verify import (
    BlueprintVerificationError,
    compiler_prototype_specs,
    verify_blueprint_structure,
)


def _rotated_pair(y: float) -> dict[str, object]:
    return {
        "blueprint": {
            "item": "blueprint",
            "entities": [
                {
                    "entity_number": 1,
                    "name": "arithmetic-combinator",
                    "direction": 4,
                    "position": {"x": 0.0, "y": 0.0},
                },
                {
                    "entity_number": 2,
                    "name": "constant-combinator",
                    "position": {"x": 0.0, "y": y},
                },
            ],
        }
    }


def test_direction_four_rotates_native_two_by_one_footprint() -> None:
    with pytest.raises(BlueprintVerificationError, match="entities 1 and 2 overlap"):
        verify_blueprint_structure(
            _rotated_pair(1.4),
            prototype_specs=compiler_prototype_specs(),
        )


def test_rotated_footprint_boundary_touching_is_still_legal() -> None:
    verify_blueprint_structure(
        _rotated_pair(1.5),
        prototype_specs=compiler_prototype_specs(),
    )
