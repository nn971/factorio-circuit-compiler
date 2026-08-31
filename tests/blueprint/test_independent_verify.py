import base64
import json
import zlib

import pytest

from factorio_circuit import Circuit, compile_circuit
from factorio_circuit.blueprint.verify import (
    BlueprintPrototypeSpec,
    BlueprintVerificationError,
    compiler_prototype_specs,
    verify_blueprint_structure,
)


def _valid_blueprint() -> dict[str, object]:
    return {
        "blueprint": {
            "item": "blueprint",
            "entities": [
                {
                    "entity_number": 1,
                    "name": "constant-combinator",
                    "position": {"x": 0.0, "y": 0.0},
                },
                {
                    "entity_number": 2,
                    "name": "arithmetic-combinator",
                    "position": {"x": 3.0, "y": 0.0},
                },
                {
                    "entity_number": 3,
                    "name": "constant-combinator",
                    "position": {"x": 6.0, "y": 0.0},
                },
            ],
            "wires": [[1, 1, 2, 1], [2, 3, 3, 1]],
        }
    }


def test_verifies_serialized_blueprint_without_layout_state() -> None:
    report = verify_blueprint_structure(
        _valid_blueprint(),
        prototype_specs=compiler_prototype_specs(),
    )

    assert report.entity_count == 3
    assert report.wire_count == 2
    assert report.prototypes == ("arithmetic-combinator", "constant-combinator")


def test_verifies_a_real_compiler_produced_blueprint_fixture() -> None:
    circuit = Circuit("i1_compiler_fixture")
    value = circuit.input("value")
    circuit.output("result", value + 1)
    result = compile_circuit(circuit, optimize=False)

    report = verify_blueprint_structure(
        result.blueprint_json,
        prototype_specs=compiler_prototype_specs(),
    )

    assert report.entity_count == len(result.blueprint_json["blueprint"]["entities"])
    assert report.wire_count == len(result.blueprint_json["blueprint"].get("wires", ()))


def test_encoded_blueprint_string_is_checked_directly() -> None:
    payload = json.dumps(_valid_blueprint(), separators=(",", ":")).encode()
    encoded = "0" + base64.b64encode(zlib.compress(payload, level=9)).decode("ascii")

    report = verify_blueprint_structure(
        encoded,
        prototype_specs=compiler_prototype_specs(),
    )

    assert report.entity_count == 3
    assert report.wire_count == 2


def test_duplicate_entity_number_is_rejected() -> None:
    artifact = _valid_blueprint()
    entities = artifact["blueprint"]["entities"]
    entities[2]["entity_number"] = 2

    with pytest.raises(BlueprintVerificationError, match="duplicate entity number 2"):
        verify_blueprint_structure(artifact, prototype_specs=compiler_prototype_specs())


def test_dangling_wire_entity_is_rejected() -> None:
    artifact = _valid_blueprint()
    artifact["blueprint"]["wires"] = [[1, 1, 99, 1]]

    with pytest.raises(BlueprintVerificationError, match="unknown entity number 99"):
        verify_blueprint_structure(artifact, prototype_specs=compiler_prototype_specs())


def test_invalid_connector_and_cross_colour_wire_are_rejected() -> None:
    artifact = _valid_blueprint()
    artifact["blueprint"]["wires"] = [[1, 3, 2, 3]]
    with pytest.raises(BlueprintVerificationError, match="cannot use connector id 3"):
        verify_blueprint_structure(artifact, prototype_specs=compiler_prototype_specs())

    artifact = _valid_blueprint()
    artifact["blueprint"]["wires"] = [[1, 1, 2, 2]]
    with pytest.raises(BlueprintVerificationError, match="connects red and green"):
        verify_blueprint_structure(artifact, prototype_specs=compiler_prototype_specs())


def test_declared_footprint_overlap_is_rejected_but_touching_is_allowed() -> None:
    artifact = _valid_blueprint()
    entities = artifact["blueprint"]["entities"]
    entities[1]["position"] = {"x": 0.9, "y": 0.0}
    with pytest.raises(BlueprintVerificationError, match="entities 1 and 2 overlap"):
        verify_blueprint_structure(artifact, prototype_specs=compiler_prototype_specs())

    touching = {
        "blueprint": {
            "entities": [
                {
                    "entity_number": 1,
                    "name": "constant-combinator",
                    "position": {"x": 0.0, "y": 0.0},
                },
                {
                    "entity_number": 2,
                    "name": "constant-combinator",
                    "position": {"x": 1.0, "y": 0.0},
                },
            ]
        }
    }
    verify_blueprint_structure(touching, prototype_specs=compiler_prototype_specs())


def test_wire_reach_uses_the_stricter_endpoint_declaration() -> None:
    artifact = {
        "blueprint": {
            "entities": [
                {
                    "entity_number": 1,
                    "name": "wide-source",
                    "position": {"x": 0.0, "y": 0.0},
                },
                {
                    "entity_number": 2,
                    "name": "wide-target",
                    "position": {"x": 8.0, "y": 0.0},
                },
            ],
            "wires": [[1, 1, 2, 1]],
        }
    }
    specs = {
        "wide-source": BlueprintPrototypeSpec(
            (0.5, 0.5),
            frozenset({1, 2}),
            maximum_wire_span=9.0,
        ),
        "wide-target": BlueprintPrototypeSpec(
            (0.5, 0.5),
            frozenset({1, 2}),
            maximum_wire_span=7.0,
        ),
    }

    with pytest.raises(BlueprintVerificationError, match="maximum declared verifier span is 7.000"):
        verify_blueprint_structure(artifact, prototype_specs=specs)


def test_unknown_prototype_requires_explicit_geometry() -> None:
    artifact = _valid_blueprint()
    artifact["blueprint"]["entities"][0]["name"] = "modded-mystery-entity"

    with pytest.raises(BlueprintVerificationError, match="no explicit verifier specification"):
        verify_blueprint_structure(artifact, prototype_specs=compiler_prototype_specs())
