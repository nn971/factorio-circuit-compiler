import pytest

from factorio_circuit.blueprint.verify import (
    BlueprintVerificationError,
    compiler_prototype_specs,
    verify_blueprint_structure,
)


def _direction_four_pair(y: float) -> dict[str, object]:
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


def _direction_zero_pair(y: float) -> dict[str, object]:
    return {
        "blueprint": {
            "item": "blueprint",
            "entities": [
                {
                    "entity_number": 1,
                    "name": "arithmetic-combinator",
                    "direction": 0,
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


def test_direction_four_rotates_native_one_by_two_footprint_to_two_by_one() -> None:
    with pytest.raises(BlueprintVerificationError, match="entities 1 and 2 overlap"):
        verify_blueprint_structure(
            _direction_four_pair(0.9),
            prototype_specs=compiler_prototype_specs(),
        )

    verify_blueprint_structure(
        _direction_four_pair(1.0),
        prototype_specs=compiler_prototype_specs(),
    )


def test_direction_zero_keeps_native_one_by_two_footprint() -> None:
    with pytest.raises(BlueprintVerificationError, match="entities 1 and 2 overlap"):
        verify_blueprint_structure(
            _direction_zero_pair(1.4),
            prototype_specs=compiler_prototype_specs(),
        )

    verify_blueprint_structure(
        _direction_zero_pair(1.5),
        prototype_specs=compiler_prototype_specs(),
    )
