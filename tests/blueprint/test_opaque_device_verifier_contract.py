from __future__ import annotations

from factorio_circuit.blueprint.connectivity_verify import (
    BlueprintEndpoint,
    BlueprintNetExpectation,
    verify_blueprint_connectivity,
)
from factorio_circuit.blueprint.geometry_verify import (
    BlueprintRegion,
    BlueprintRigidComponentExpectation,
    BlueprintRigidMemberExpectation,
    verify_blueprint_geometry,
)
from factorio_circuit.blueprint.verify import (
    BlueprintPrototypeSpec,
    verify_blueprint_structure,
)
from factorio_circuit.devices import AssemblerDevice

_MEMBER_POSITIONS: dict[int, tuple[float, float]] = {
    1: (1.5, 5.5),
    2: (4.0, 5.5),
    3: (1.5, 10.5),
    4: (4.0, 10.5),
    5: (8.5, 8.5),
    6: (12.5, 8.5),
    7: (14.5, 4.5),
    8: (14.5, 6.5),
    9: (14.5, 8.5),
    10: (17.0, 6.5),
    11: (20.0, 6.5),
    12: (14.5, 11.5),
    13: (18.0, 11.5),
    14: (14.5, 14.0),
    15: (18.0, 14.0),
    16: (8.5, 11.5),
    17: (8.5, 10.5),
    18: (10.5, 8.5),
    19: (11.5, 8.5),
    20: (1.5, 15.5),
    21: (4.0, 15.5),
    22: (8.5, 16.0),
    23: (12.0, 17.5),
    24: (16.0, 15.5),
    25: (18.0, 17.5),
}

_CONSTANT_IDS = frozenset({1, 3, 6, 10, 11, 13, 15, 20, 23, 25})
_ARITHMETIC_IDS = frozenset({2, 4, 7, 8, 9, 12, 14, 21, 22, 24})


def _prototype_for_member(entity_id: int) -> str:
    if entity_id in _CONSTANT_IDS:
        return "constant-combinator"
    if entity_id in _ARITHMETIC_IDS:
        return "arithmetic-combinator"
    return {
        5: "assembling-machine-3",
        16: "requester-chest",
        17: "bulk-inserter",
        18: "bulk-inserter",
        19: "active-provider-chest",
    }[entity_id]


def _prototype_specs() -> dict[str, BlueprintPrototypeSpec]:
    single = BlueprintPrototypeSpec(
        (0.5, 0.5),
        frozenset({1, 2}),
        maximum_wire_span=9.0,
    )
    return {
        "constant-combinator": single,
        "arithmetic-combinator": BlueprintPrototypeSpec(
            (0.5, 1.0),
            frozenset({1, 2, 3, 4}),
            maximum_wire_span=9.0,
            rotates_half_extent=True,
        ),
        "assembling-machine-3": BlueprintPrototypeSpec(
            (1.5, 1.5),
            frozenset({1, 2}),
            maximum_wire_span=9.0,
        ),
        "requester-chest": single,
        "active-provider-chest": single,
        "bulk-inserter": single,
    }


def _net(name: str, *endpoints: tuple[int, int]) -> BlueprintNetExpectation:
    return BlueprintNetExpectation(
        name,
        tuple(BlueprintEndpoint(entity_id, connector_id) for entity_id, connector_id in endpoints),
    )


def _expected_physical_nets() -> tuple[BlueprintNetExpectation, ...]:
    return (
        _net("recipe-input", (1, 2), (2, 2)),
        _net("enable-input", (3, 2), (4, 2)),
        _net("machine-command", (2, 4), (4, 4), (5, 2), (17, 2)),
        _net("requester-demand-input", (20, 2), (21, 2)),
        _net("requester-demand-output", (21, 4), (16, 2)),
        _net("requester-contents-input", (16, 1), (22, 1)),
        _net("requester-contents-output", (22, 3), (23, 1)),
        _net("provider-contents-input", (19, 1), (24, 1)),
        _net("provider-contents-output", (24, 3), (25, 1)),
        _net("raw-machine-output", (5, 1), (6, 1), (7, 1), (8, 1), (9, 1), (12, 1), (14, 1)),
        _net("clean-ingredients", (7, 3), (8, 3), (9, 3), (10, 1), (11, 1)),
        _net("working-output", (12, 3), (13, 1)),
        _net("finished-output", (14, 3), (15, 1)),
    )


def _component_expectation() -> BlueprintRigidComponentExpectation:
    return BlueprintRigidComponentExpectation(
        "assembler-v3",
        origin=(0.0, 0.0),
        members=tuple(
            BlueprintRigidMemberExpectation(
                entity_id,
                position,
                _prototype_for_member(entity_id),
            )
            for entity_id, position in sorted(_MEMBER_POSITIONS.items())
        ),
        footprints=(BlueprintRegion(1.0, 4.0, 20.5, 18.0),),
        keepouts=(BlueprintRegion(7.0, 1.0, 10.0, 4.0),),
        adapter_regions=(BlueprintRegion(11.0, 1.0, 13.0, 4.0),),
    )


def test_opaque_assembler_blueprint_satisfies_independent_i1_i2_i3_contract() -> None:
    blueprint = AssemblerDevice(label="I4 independent verifier fixture").build().blueprint
    specs = _prototype_specs()

    structure = verify_blueprint_structure(blueprint, prototype_specs=specs)
    assert structure.entity_count == 25
    assert structure.wire_count == 23

    connectivity = verify_blueprint_connectivity(
        blueprint,
        prototype_specs=specs,
        expected_nets=_expected_physical_nets(),
    )
    assert connectivity.network_count == 13
    assert connectivity.verified_nets == tuple(net.name for net in _expected_physical_nets())

    geometry = verify_blueprint_geometry(
        blueprint,
        prototype_specs=specs,
        components=(_component_expectation(),),
    )
    assert geometry.verified_components == ("assembler-v3",)
