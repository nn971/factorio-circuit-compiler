from __future__ import annotations

import pytest

from examples.oracle_provider_mixed_probe import (
    ANCHORED_SENSOR_POSITION,
    compile_mixed_provider_probe,
)
from factorio_circuit.blueprint.connectivity_verify import (
    BlueprintEndpoint,
    BlueprintNetExpectation,
    BlueprintPortDirection,
    BlueprintPublicPortExpectation,
    verify_blueprint_connectivity,
)
from factorio_circuit.blueprint.geometry_verify import (
    BlueprintAnchorExpectation,
    BlueprintRegion,
    BlueprintRigidComponentExpectation,
    BlueprintRigidMemberExpectation,
    verify_blueprint_geometry,
)
from factorio_circuit.blueprint.verify import (
    BlueprintPrototypeSpec,
    compiler_prototype_specs,
    decode_blueprint_string,
    verify_blueprint_structure,
)

_MEMBER_CONTRACT = (
    ("ASSEMBLER PORT recipe — INPUT Level vector; GREEN", "constant-combinator", (1.5, 5.5)),
    ("ASSEMBLER DEVICE isolate recipe input", "arithmetic-combinator", (4.0, 5.5)),
    ("ASSEMBLER PORT enable — INPUT signal-E Level; GREEN", "constant-combinator", (1.5, 10.5)),
    ("ASSEMBLER DEVICE isolate enable input", "arithmetic-combinator", (4.0, 10.5)),
    ("ASSEMBLER DEVICE machine", "assembling-machine-3", (8.5, 8.5)),
    (
        "ASSEMBLER DEVICE raw ingredient/status output relay",
        "constant-combinator",
        (12.5, 8.5),
    ),
    ("ASSEMBLER DEVICE copy ingredients", "arithmetic-combinator", (14.5, 4.5)),
    ("ASSEMBLER DEVICE strip working from ingredients", "arithmetic-combinator", (14.5, 6.5)),
    ("ASSEMBLER DEVICE strip finished from ingredients", "arithmetic-combinator", (14.5, 8.5)),
    ("ASSEMBLER DEVICE clean ingredient merge", "constant-combinator", (17.0, 6.5)),
    (
        "ASSEMBLER PORT ingredients — OUTPUT Level vector; RED",
        "constant-combinator",
        (20.0, 6.5),
    ),
    ("ASSEMBLER DEVICE extract working", "arithmetic-combinator", (14.5, 11.5)),
    (
        "ASSEMBLER PORT working — OUTPUT signal-W Level; RED",
        "constant-combinator",
        (18.0, 11.5),
    ),
    ("ASSEMBLER DEVICE extract finished event", "arithmetic-combinator", (14.5, 14.0)),
    (
        "ASSEMBLER PORT finished — OUTPUT signal-F Event; RED",
        "constant-combinator",
        (18.0, 14.0),
    ),
    ("ASSEMBLER DEVICE requester chest", "requester-chest", (8.5, 11.5)),
    ("ASSEMBLER DEVICE requester -> assembler feeder", "bulk-inserter", (8.5, 10.5)),
    ("ASSEMBLER DEVICE assembler -> provider output", "bulk-inserter", (10.5, 8.5)),
    ("ASSEMBLER DEVICE provider chest", "active-provider-chest", (11.5, 8.5)),
    (
        "ASSEMBLER PORT requester_demand — INPUT Level vector; GREEN",
        "constant-combinator",
        (1.5, 15.5),
    ),
    ("ASSEMBLER DEVICE isolate requester demand", "arithmetic-combinator", (4.0, 15.5)),
    ("ASSEMBLER DEVICE isolate requester contents", "arithmetic-combinator", (8.5, 16.0)),
    (
        "ASSEMBLER PORT requester_contents — OUTPUT Level vector; RED",
        "constant-combinator",
        (12.0, 17.5),
    ),
    ("ASSEMBLER DEVICE isolate provider contents", "arithmetic-combinator", (16.0, 15.5)),
    (
        "ASSEMBLER PORT provider_contents — OUTPUT Level vector; RED",
        "constant-combinator",
        (18.0, 17.5),
    ),
)


def _prototype_specs() -> dict[str, BlueprintPrototypeSpec]:
    specs = compiler_prototype_specs()
    specs["constant-combinator"] = BlueprintPrototypeSpec(
        (0.5, 0.5),
        frozenset({1, 2}),
        maximum_wire_span=9.0,
    )
    specs["arithmetic-combinator"] = BlueprintPrototypeSpec(
        (0.5, 1.0),
        frozenset({1, 2, 3, 4}),
        maximum_wire_span=9.0,
        rotates_half_extent=True,
    )
    for prototype, half_extent in {
        "assembling-machine-3": (1.5, 1.5),
        "requester-chest": (0.5, 0.5),
        "active-provider-chest": (0.5, 0.5),
        "bulk-inserter": (0.5, 0.5),
    }.items():
        specs[prototype] = BlueprintPrototypeSpec(
            half_extent,
            frozenset({1, 2}),
            maximum_wire_span=9.0,
        )
    return specs


def _entities(blueprint):
    return blueprint["entities"]


def _entity_id_with_exact_description(blueprint, description: str) -> int:
    matches = [
        entity for entity in _entities(blueprint) if entity.get("player_description") == description
    ]
    assert len(matches) == 1, description
    return int(matches[0]["entity_number"])


def _entity_id_with_description_suffix(blueprint, suffix: str) -> int:
    matches = [
        entity
        for entity in _entities(blueprint)
        if str(entity.get("player_description", "")).endswith(suffix)
    ]
    assert len(matches) == 1, suffix
    return int(matches[0]["entity_number"])


def _public_marker_id(blueprint, phrase: str) -> int:
    matches = [
        entity
        for entity in _entities(blueprint)
        if str(entity.get("player_description", "")).startswith("[FCC #")
        and f" {phrase} —" in str(entity.get("player_description", ""))
    ]
    assert len(matches) == 1, phrase
    return int(matches[0]["entity_number"])


def _component_expectation(blueprint) -> BlueprintRigidComponentExpectation:
    return BlueprintRigidComponentExpectation(
        "e3-assembler-device",
        origin=(0.0, 0.0),
        members=tuple(
            BlueprintRigidMemberExpectation(
                _entity_id_with_exact_description(blueprint, description),
                position,
                prototype,
            )
            for description, prototype, position in _MEMBER_CONTRACT
        ),
        footprints=(BlueprintRegion(1.0, 4.0, 20.5, 18.0),),
        keepouts=(BlueprintRegion(7.0, 1.0, 10.0, 4.0),),
        adapter_regions=(BlueprintRegion(11.0, 1.0, 13.0, 4.0),),
    )


@pytest.mark.acceptance
def test_i4_e3_exact_serialized_artifact_satisfies_independent_verifier_stack() -> None:
    result = compile_mixed_provider_probe()
    encoded = result.blueprint_string
    decoded = decode_blueprint_string(encoded)
    blueprint = decoded["blueprint"]
    specs = _prototype_specs()

    verify_blueprint_structure(encoded, prototype_specs=specs)

    recipe_dock = _entity_id_with_exact_description(
        blueprint,
        "ASSEMBLER PORT recipe — INPUT Level vector; GREEN",
    )
    ingredients_dock = _entity_id_with_exact_description(
        blueprint,
        "ASSEMBLER PORT ingredients — OUTPUT Level vector; RED",
    )
    recipe_marker = _public_marker_id(blueprint, "INPUT recipe")
    ingredients_marker = _public_marker_id(blueprint, "OUTPUT ingredients")

    connectivity = verify_blueprint_connectivity(
        encoded,
        prototype_specs=specs,
        expected_nets=(
            BlueprintNetExpectation(
                "recipe-to-provider",
                (
                    BlueprintEndpoint(recipe_marker, 2),
                    BlueprintEndpoint(recipe_dock, 2),
                ),
            ),
            BlueprintNetExpectation(
                "ingredients-from-provider",
                (
                    BlueprintEndpoint(ingredients_dock, 1),
                    BlueprintEndpoint(ingredients_marker, 1),
                ),
            ),
        ),
        expected_public_ports=(
            BlueprintPublicPortExpectation("x", BlueprintPortDirection.INPUT),
            BlueprintPublicPortExpectation(
                "recipe",
                BlueprintPortDirection.INPUT,
                (BlueprintEndpoint(recipe_dock, 2),),
            ),
            BlueprintPublicPortExpectation("logic", BlueprintPortDirection.OUTPUT),
            BlueprintPublicPortExpectation(
                "ingredients",
                BlueprintPortDirection.OUTPUT,
                (BlueprintEndpoint(ingredients_dock, 1),),
            ),
        ),
    )
    assert connectivity.verified_nets == (
        "recipe-to-provider",
        "ingredients-from-provider",
    )

    anchored_sensor = _entity_id_with_description_suffix(
        blueprint,
        "ORACLE anchored_sensor: constant 7",
    )
    geometry = verify_blueprint_geometry(
        encoded,
        prototype_specs=specs,
        anchors=(
            BlueprintAnchorExpectation(
                "e3-world-sensor",
                anchored_sensor,
                1,
                ANCHORED_SENSOR_POSITION,
                "constant-combinator",
            ),
        ),
        components=(_component_expectation(blueprint),),
    )
    assert geometry.verified_anchors == ("e3-world-sensor",)
    assert geometry.verified_components == ("e3-assembler-device",)
