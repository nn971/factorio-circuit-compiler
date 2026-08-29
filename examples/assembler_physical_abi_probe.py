"""Milestone D4 probe: real AssemblerDevice through D1 -> D2 -> D3 -> serialization.

The reusable 25-entity assembler device is imported as opaque physical objects with explicit
prototype geometry. Two compiler-facing annotation markers expose the device's ``recipe`` input and
``ingredients`` output. The whole device is then translated rigidly, both markers are pinned to
far-away exact anchors, and D3 reserves/fresh-routes the resulting external seams before the mixed
layout is serialized back to a Factorio blueprint.
"""

from __future__ import annotations

from factorio_circuit.blueprint.opaque_layout_encode import (
    encode_layout_blueprint_string_with_opaque,
    layout_to_blueprint_json_with_opaque,
)
from factorio_circuit.blueprint.routing import VANILLA_COMBINATOR_WIRE_REACH
from factorio_circuit.devices import AssemblerDevice
from factorio_circuit.ir.physical import (
    Connector,
    ConstantCombinator,
    InputPort,
    OutputPort,
    PhysicalCircuit,
    WireConnection,
    WireEndpoint,
)
from factorio_circuit.synthesis.anchored_interface_routing import (
    AnchoredInterfaceLayoutProblem,
    AnchoredInterfaceRoutingResult,
    PublicPortAnchorConstraint,
    route_anchored_interfaces_transactionally,
)
from factorio_circuit.synthesis.blueprint_component import (
    BlueprintConnectorShape,
    BlueprintEntityPhysicalSpec,
    import_blueprint_layout,
)
from factorio_circuit.synthesis.component_geometry import (
    ComponentAccessPoint,
    ComponentLayoutOptimizationProblem,
    ComponentRegion,
    validate_component_layout_problem,
)
from factorio_circuit.synthesis.imported_component_geometry import (
    imported_layout_as_rigid_component,
)
from factorio_circuit.synthesis.layout import Layout, LayoutWire
from factorio_circuit.synthesis.layout_optimizer import LayoutOptimizationProblem, LegalPlacementLattice
from factorio_circuit.synthesis.rigid_component_translation import (
    RigidComponentTranslationResult,
    translate_rigid_component_transactionally,
)

DEVICE_ORIGIN = (0.0, 0.0)
TRANSLATED_DEVICE_ORIGIN = (24.0, 0.0)
RECIPE_ANCHOR = (-15.5, 5.5)
INGREDIENTS_ANCHOR = (60.5, 6.5)
RECIPE_MARKER_ID = 26
INGREDIENTS_MARKER_ID = 27

ASSEMBLER_PROTOTYPE_SPECS = {
    "constant-combinator": BlueprintEntityPhysicalSpec((0.5, 0.5)),
    "arithmetic-combinator": BlueprintEntityPhysicalSpec(
        (1.0, 0.5), BlueprintConnectorShape.INPUT_OUTPUT
    ),
    "assembling-machine-3": BlueprintEntityPhysicalSpec((1.5, 1.5)),
    "requester-chest": BlueprintEntityPhysicalSpec((0.5, 0.5)),
    "active-provider-chest": BlueprintEntityPhysicalSpec((0.5, 0.5)),
    "bulk-inserter": BlueprintEntityPhysicalSpec((0.5, 0.5)),
}


def _routing_lattice() -> LegalPlacementLattice:
    # Keep the benchmark workspace bounded to the real device/anchor neighborhood. D3 constructs
    # its own deterministic interface corridors; this lattice is only the residual relay workspace.
    sites = tuple(
        (x_steps / 2.0, y_steps / 2.0) for y_steps in range(0, 41) for x_steps in range(-34, 127)
    )
    return LegalPlacementLattice(unit_sites=sites, wide_sites=sites)


def build_assembler_physical_abi_problem() -> ComponentLayoutOptimizationProblem:
    device = AssemblerDevice(label="AssemblerDevice D4 physical ABI probe").build()
    imported = import_blueprint_layout(
        device.blueprint,
        prototype_specs=ASSEMBLER_PROTOTYPE_SPECS,
        name="AssemblerDevice D4 physical ABI probe",
    )
    base_layout = imported.layout
    recipe = device.port("recipe")
    ingredients = device.port("ingredients")

    recipe_marker = ConstantCombinator(
        RECIPE_MARKER_ID, description="D4 external recipe marker", annotation_only=True
    )
    ingredients_marker = ConstantCombinator(
        INGREDIENTS_MARKER_ID, description="D4 external ingredients marker", annotation_only=True
    )
    circuit = PhysicalCircuit(
        base_layout.circuit.name,
        entities=[*base_layout.circuit.entities, recipe_marker, ingredients_marker],
        connections=[
            *base_layout.circuit.connections,
            WireConnection(
                WireEndpoint(RECIPE_MARKER_ID, Connector.SINGLE),
                WireEndpoint(recipe.endpoint.entity_number, Connector.SINGLE),
                recipe.endpoint.wire,
            ),
            WireConnection(
                WireEndpoint(INGREDIENTS_MARKER_ID, Connector.SINGLE),
                WireEndpoint(ingredients.endpoint.entity_number, Connector.SINGLE),
                ingredients.endpoint.wire,
            ),
        ],
        inputs=[InputPort("recipe", RECIPE_MARKER_ID, None)],
        outputs=[OutputPort("ingredients", INGREDIENTS_MARKER_ID, None, 0)],
    )
    positions = {
        **base_layout.positions,
        RECIPE_MARKER_ID: (-1.5, 5.5),
        INGREDIENTS_MARKER_ID: (23.0, 6.5),
    }
    wires = (
        *base_layout.wires,
        LayoutWire(
            RECIPE_MARKER_ID,
            recipe.endpoint.connector_id,
            recipe.endpoint.entity_number,
            recipe.endpoint.connector_id,
            recipe.endpoint.wire,
        ),
        LayoutWire(
            INGREDIENTS_MARKER_ID,
            ingredients.endpoint.connector_id,
            ingredients.endpoint.entity_number,
            ingredients.endpoint.connector_id,
            ingredients.endpoint.wire,
        ),
    )
    layout = Layout(circuit, positions, (), wires, (), ())
    base_problem = LayoutOptimizationProblem(
        layout,
        _routing_lattice(),
        # The imported assembler is an already-materialized Factorio blueprint and contains one
        # legitimate 8.322-tile device-internal circuit span. Validate/reroute it against the actual
        # vanilla 9-tile envelope instead of the compiler's conservative 7-tile construction
        # default.
        safe_wire_span=VANILLA_COMBINATOR_WIRE_REACH,
    )
    component = imported_layout_as_rigid_component(
        imported,
        "assembler-device",
        origin=DEVICE_ORIGIN,
        footprints=(ComponentRegion(1.0, 4.0, 20.5, 18.0),),
        keepouts=(ComponentRegion(7.0, 1.0, 10.0, 4.0),),
        adapter_regions=(ComponentRegion(11.0, 1.0, 13.0, 4.0),),
        access_points=(
            ComponentAccessPoint("recipe-west", (1.0, 5.5)),
            ComponentAccessPoint("ingredients-east", (20.5, 6.5)),
        ),
        allowed_origins=(DEVICE_ORIGIN, TRANSLATED_DEVICE_ORIGIN),
    )
    problem = ComponentLayoutOptimizationProblem(base_problem, (component,))
    validate_component_layout_problem(problem)
    return problem


def translate_assembler_physical_abi_probe() -> RigidComponentTranslationResult:
    return translate_rigid_component_transactionally(
        build_assembler_physical_abi_problem(), "assembler-device", TRANSLATED_DEVICE_ORIGIN
    )


def route_assembler_physical_abi_probe() -> AnchoredInterfaceRoutingResult:
    translated = translate_assembler_physical_abi_probe()
    if not translated.succeeded:
        raise ValueError(f"D4 rigid translation failed: {translated.failure}")
    anchored = AnchoredInterfaceLayoutProblem(
        translated.problem,
        (
            PublicPortAnchorConstraint(
                "assembler-recipe",
                "input",
                "recipe",
                "assembler-device",
                "recipe-west",
                RECIPE_ANCHOR,
                max_detour_tiles=4,
            ),
            PublicPortAnchorConstraint(
                "assembler-ingredients",
                "output",
                "ingredients",
                "assembler-device",
                "ingredients-east",
                INGREDIENTS_ANCHOR,
                max_detour_tiles=4,
            ),
        ),
    )
    return route_anchored_interfaces_transactionally(anchored)


def build_assembler_physical_abi_probe() -> dict[str, object]:
    routed = route_assembler_physical_abi_probe()
    if not routed.succeeded:
        raise ValueError(f"D4 anchored routing failed: {routed.failure}")
    result = layout_to_blueprint_json_with_opaque(routed.problem.component_problem.layout_problem.layout)
    return result["blueprint"]


def generate_assembler_physical_abi_probe_string() -> str:
    routed = route_assembler_physical_abi_probe()
    if not routed.succeeded:
        raise ValueError(f"D4 anchored routing failed: {routed.failure}")
    return encode_layout_blueprint_string_with_opaque(
        routed.problem.component_problem.layout_problem.layout
    )


def main() -> None:
    print(generate_assembler_physical_abi_probe_string())


if __name__ == "__main__":
    main()
