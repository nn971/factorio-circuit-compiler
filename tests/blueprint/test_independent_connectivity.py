import copy

import pytest

from factorio_circuit import Circuit, compile_circuit
from factorio_circuit.blueprint.connectivity_verify import (
    BlueprintEndpoint,
    BlueprintNetExpectation,
    BlueprintPortDirection,
    BlueprintPublicPortExpectation,
    verify_blueprint_connectivity,
)
from factorio_circuit.blueprint.verify import (
    BlueprintVerificationError,
    compiler_prototype_specs,
)


def _network_blueprint() -> dict[str, object]:
    return {
        "blueprint": {
            "item": "blueprint",
            "entities": [
                {
                    "entity_number": entity_id,
                    "name": "constant-combinator",
                    "position": {"x": float((entity_id - 1) * 2), "y": 0.0},
                }
                for entity_id in range(1, 5)
            ],
            "wires": [
                [1, 1, 2, 1],
                [3, 1, 4, 1],
                [1, 2, 3, 2],
            ],
        }
    }


def _network_expectations() -> tuple[BlueprintNetExpectation, ...]:
    return (
        BlueprintNetExpectation(
            "red-left",
            (BlueprintEndpoint(1, 1), BlueprintEndpoint(2, 1)),
        ),
        BlueprintNetExpectation(
            "red-right",
            (BlueprintEndpoint(3, 1), BlueprintEndpoint(4, 1)),
        ),
        BlueprintNetExpectation(
            "green-cross",
            (BlueprintEndpoint(1, 2), BlueprintEndpoint(3, 2)),
        ),
    )


def _public_port_blueprint() -> dict[str, object]:
    return {
        "blueprint": {
            "item": "blueprint",
            "entities": [
                {
                    "entity_number": 1,
                    "name": "constant-combinator",
                    "position": {"x": 0.0, "y": 0.0},
                    "player_description": (
                        "[FCC #1 | marker] INPUT value — inject value on [signal-A] here"
                    ),
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
                    "player_description": (
                        "[FCC #3 | marker] OUTPUT result — [signal-B], phase +1 tick(s)"
                    ),
                },
            ],
            "wires": [[1, 1, 2, 1], [2, 3, 3, 1]],
        }
    }


def _public_port_expectations() -> tuple[BlueprintPublicPortExpectation, ...]:
    return (
        BlueprintPublicPortExpectation(
            "value",
            BlueprintPortDirection.INPUT,
            peers=(BlueprintEndpoint(2, 1),),
        ),
        BlueprintPublicPortExpectation(
            "result",
            BlueprintPortDirection.OUTPUT,
            peers=(BlueprintEndpoint(2, 3),),
        ),
    )


def test_reconstructs_red_and_green_connected_components() -> None:
    report = verify_blueprint_connectivity(
        _network_blueprint(),
        prototype_specs=compiler_prototype_specs(),
        expected_nets=_network_expectations(),
    )

    assert report.network_count == 3
    assert report.verified_nets == ("red-left", "red-right", "green-cross")
    assert report.public_ports == ()


def test_missing_serialized_wire_breaks_expected_net_equivalence() -> None:
    artifact = _network_blueprint()
    artifact["blueprint"]["wires"].remove([3, 1, 4, 1])

    with pytest.raises(BlueprintVerificationError, match="'red-right' is disconnected"):
        verify_blueprint_connectivity(
            artifact,
            prototype_specs=compiler_prototype_specs(),
            expected_nets=_network_expectations(),
        )


def test_accidental_serialized_short_between_expected_nets_is_rejected() -> None:
    artifact = _network_blueprint()
    artifact["blueprint"]["wires"].append([2, 1, 3, 1])

    with pytest.raises(
        BlueprintVerificationError,
        match="'red-left' and 'red-right' are unexpectedly shorted",
    ):
        verify_blueprint_connectivity(
            artifact,
            prototype_specs=compiler_prototype_specs(),
            expected_nets=_network_expectations(),
        )


def test_expected_physical_net_contract_rejects_overlapping_groups() -> None:
    expectations = (
        BlueprintNetExpectation("a", (BlueprintEndpoint(1, 1),)),
        BlueprintNetExpectation("b", (BlueprintEndpoint(1, 1),)),
    )

    with pytest.raises(ValueError, match="appears in both 'a' and 'b'"):
        verify_blueprint_connectivity(
            _network_blueprint(),
            prototype_specs=compiler_prototype_specs(),
            expected_nets=expectations,
        )


def test_public_marker_contract_is_reconstructed_from_serialized_annotations() -> None:
    report = verify_blueprint_connectivity(
        _public_port_blueprint(),
        prototype_specs=compiler_prototype_specs(),
        expected_public_ports=_public_port_expectations(),
    )

    actual = [
        (port.direction, port.name, port.entity_id, port.connector_ids)
        for port in report.public_ports
    ]
    assert actual == [
        (BlueprintPortDirection.INPUT, "value", 1, (1,)),
        (BlueprintPortDirection.OUTPUT, "result", 3, (1,)),
    ]


def test_public_port_peer_must_be_reachable_from_serialized_marker() -> None:
    artifact = _public_port_blueprint()
    artifact["blueprint"]["wires"].remove([1, 1, 2, 1])

    with pytest.raises(BlueprintVerificationError, match="not connected to exactly one"):
        verify_blueprint_connectivity(
            artifact,
            prototype_specs=compiler_prototype_specs(),
            expected_public_ports=_public_port_expectations(),
        )


def test_public_port_contract_detects_renamed_or_extra_serialized_marker() -> None:
    artifact = _public_port_blueprint()
    artifact["blueprint"]["entities"][2]["player_description"] = (
        "[FCC #3 | marker] OUTPUT renamed — [signal-B], phase +1 tick(s)"
    )

    with pytest.raises(BlueprintVerificationError, match="public-port contract mismatch"):
        verify_blueprint_connectivity(
            artifact,
            prototype_specs=compiler_prototype_specs(),
            expected_public_ports=_public_port_expectations(),
        )


def test_public_marker_cannot_be_wired_to_both_factorio_colours() -> None:
    artifact = _public_port_blueprint()
    artifact["blueprint"]["wires"].append([1, 2, 2, 2])

    with pytest.raises(BlueprintVerificationError, match="wired to both red and green"):
        verify_blueprint_connectivity(
            artifact,
            prototype_specs=compiler_prototype_specs(),
            expected_public_ports=_public_port_expectations(),
        )


def test_public_marker_annotation_entity_number_must_match_serialized_entity() -> None:
    artifact = _public_port_blueprint()
    artifact["blueprint"]["entities"][0]["player_description"] = (
        "[FCC #9 | marker] INPUT value — inject value on [signal-A] here"
    )

    with pytest.raises(BlueprintVerificationError, match="annotation id 9 does not match"):
        verify_blueprint_connectivity(
            artifact,
            prototype_specs=compiler_prototype_specs(),
            expected_public_ports=_public_port_expectations(),
        )


def test_real_compiler_blueprint_public_ports_are_visible_without_layout_state() -> None:
    circuit = Circuit("i2_public_ports")
    value = circuit.input("value")
    circuit.output("result", value + 1)
    result = compile_circuit(circuit, optimize=False)

    expectations = (
        BlueprintPublicPortExpectation("value", BlueprintPortDirection.INPUT),
        BlueprintPublicPortExpectation("result", BlueprintPortDirection.OUTPUT),
    )
    report = verify_blueprint_connectivity(
        result.blueprint_string,
        prototype_specs=compiler_prototype_specs(),
        expected_public_ports=expectations,
    )

    assert {(port.direction, port.name) for port in report.public_ports} == {
        (BlueprintPortDirection.INPUT, "value"),
        (BlueprintPortDirection.OUTPUT, "result"),
    }
    assert all(len(port.connector_ids) == 1 for port in report.public_ports)


def test_connectivity_verification_does_not_mutate_serialized_fixture() -> None:
    artifact = _public_port_blueprint()
    before = copy.deepcopy(artifact)

    verify_blueprint_connectivity(
        artifact,
        prototype_specs=compiler_prototype_specs(),
        expected_public_ports=_public_port_expectations(),
    )

    assert artifact == before
