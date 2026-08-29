from examples.assembler_physical_abi_probe import (
    INGREDIENTS_ANCHOR,
    INGREDIENTS_MARKER_ID,
    RECIPE_ANCHOR,
    RECIPE_MARKER_ID,
    TRANSLATED_DEVICE_ORIGIN,
    build_assembler_physical_abi_problem,
    route_assembler_physical_abi_probe,
)
from factorio_circuit.blueprint.opaque_layout_encode import (
    encode_layout_blueprint_string_with_opaque,
)
from factorio_circuit.devices import AssemblerDevice
from factorio_circuit.devices._blueprint import decode_blueprint
from factorio_circuit.ir.physical import OpaqueSingleConnectorEntity
from factorio_circuit.synthesis.anchored_interface_routing import (
    validate_anchored_interface_routing,
)
from factorio_circuit.synthesis.component_geometry import validate_component_layout_problem


def _source_positions() -> dict[int, tuple[float, float]]:
    entities = AssemblerDevice().build().blueprint["entities"]
    return {
        entity["entity_number"]: (entity["position"]["x"], entity["position"]["y"])
        for entity in entities
    }


def test_real_assembler_device_passes_d1_d2_d3_and_serialization() -> None:
    initial = build_assembler_physical_abi_problem()
    validate_component_layout_problem(initial)
    assert len(initial.components[0].members) == 25
    machine = initial.layout_problem.layout.circuit.entity_by_id(5)
    assert isinstance(machine, OpaqueSingleConnectorEntity)
    assert machine.prototype == "assembling-machine-3"
    assert machine.physical_half_extent == (1.5, 1.5)

    routed = route_assembler_physical_abi_probe()

    assert routed.succeeded, routed.failure
    assert len(routed.reservations) == 2
    assert all(reservation.relay_ids for reservation in routed.reservations)
    problem = routed.problem.component_problem
    layout = problem.layout_problem.layout
    assert problem.components[0].origin == TRANSLATED_DEVICE_ORIGIN

    source = _source_positions()
    for entity_id, position in source.items():
        assert layout.positions[entity_id] == (position[0] + 24.0, position[1])
    assert layout.positions[RECIPE_MARKER_ID] == RECIPE_ANCHOR
    assert layout.positions[INGREDIENTS_MARKER_ID] == INGREDIENTS_ANCHOR
    validate_component_layout_problem(problem)
    validate_anchored_interface_routing(routed.problem, routed.reservations)

    encoded = encode_layout_blueprint_string_with_opaque(layout)
    decoded = decode_blueprint(encoded)
    entities = {entity["entity_number"]: entity for entity in decoded["entities"]}
    source_blueprint_entities = AssemblerDevice().build().blueprint["entities"]
    source_entities = {entity["entity_number"]: entity for entity in source_blueprint_entities}

    # The real machine moved rigidly while its assembler-specific control behavior survived exactly.
    assert entities[5]["name"] == "assembling-machine-3"
    assert entities[5]["position"] == {"x": 32.5, "y": 8.5}
    assert entities[5]["control_behavior"] == source_entities[5]["control_behavior"]
    assert entities[16]["name"] == "requester-chest"
    assert entities[16]["control_behavior"] == source_entities[16]["control_behavior"]
    assert entities[17]["name"] == "bulk-inserter"
    assert entities[17]["control_behavior"] == source_entities[17]["control_behavior"]

    # D3 public pins are serialized exactly and every imported device member is still present.
    assert entities[RECIPE_MARKER_ID]["position"] == {
        "x": RECIPE_ANCHOR[0],
        "y": RECIPE_ANCHOR[1],
    }
    assert entities[INGREDIENTS_MARKER_ID]["position"] == {
        "x": INGREDIENTS_ANCHOR[0],
        "y": INGREDIENTS_ANCHOR[1],
    }
    assert set(range(1, 26)) <= set(entities)
