"""Milestone E3 probe: ordinary logic + free/anchored providers + real rigid device.

This is the mixed compiler integration benchmark for Milestone E.  One Level program contains:

- ordinary scalar arithmetic;
- a freely placeable scalar oracle provider;
- a symbolically anchored scalar oracle provider;
- the real 25-entity :class:`AssemblerDevice` as a rigid provider component.

The deterministic ``recipe`` vector is bound to the assembler's GREEN recipe port and the device's
RED ``ingredients`` observation realizes a vector oracle.  Full compilation must produce one mixed,
exact-valid blueprint; E2 construction proxies must not survive serialization.
"""

from __future__ import annotations

from dataclasses import dataclass

from factorio_circuit import (
    AnchoredPlacement,
    Circuit,
    OraclePhysicalContext,
    OraclePortDisposition,
    ProviderRigidComponentProduct,
    ScalarConstantOracleProvider,
)
from factorio_circuit.devices import AssemblerDevice
from factorio_circuit.synthesis import BlueprintConnectorShape, BlueprintEntityPhysicalSpec
from factorio_circuit.synthesis.component_geometry import ComponentRegion
from factorio_circuit.synthesis.placement import PlacementOptions

ASSEMBLER_ORIGIN = (0.0, 0.0)
ANCHORED_SENSOR_POSITION = (-12.5, -4.5)

ASSEMBLER_PROTOTYPE_SPECS = {
    "constant-combinator": BlueprintEntityPhysicalSpec((0.5, 0.5)),
    "arithmetic-combinator": BlueprintEntityPhysicalSpec(
        (1.0, 0.5),
        BlueprintConnectorShape.INPUT_OUTPUT,
    ),
    "assembling-machine-3": BlueprintEntityPhysicalSpec((1.5, 1.5)),
    "requester-chest": BlueprintEntityPhysicalSpec((0.5, 0.5)),
    "active-provider-chest": BlueprintEntityPhysicalSpec((0.5, 0.5)),
    "bulk-inserter": BlueprintEntityPhysicalSpec((0.5, 0.5)),
}

_ASSEMBLER_DEVICE = AssemblerDevice(label="AssemblerDevice E3 mixed integration").build()


@dataclass(frozen=True, slots=True)
class AssemblerIngredientsOracleProvider:
    """Bind deterministic recipe input and expose the assembler ingredient vector as an oracle."""

    def materialize(self, context: OraclePhysicalContext) -> OraclePortDisposition:
        context.add_rigid_component(
            ProviderRigidComponentProduct(
                "e3-assembler-device",
                _ASSEMBLER_DEVICE,
                ASSEMBLER_PROTOTYPE_SPECS,
                origin=ASSEMBLER_ORIGIN,
                footprints=(ComponentRegion(1.0, 4.0, 20.5, 18.0),),
                keepouts=(ComponentRegion(7.0, 1.0, 10.0, 4.0),),
                adapter_regions=(ComponentRegion(11.0, 1.0, 13.0, 4.0),),
                allowed_origins=(ASSEMBLER_ORIGIN,),
                internal_wire_span=9.0,
                port_bindings=(
                    context.component_input_binding("recipe", _ASSEMBLER_DEVICE, "recipe"),
                    context.component_output_binding(_ASSEMBLER_DEVICE, "ingredients"),
                ),
            )
        )
        return OraclePortDisposition.CONSUMED


def build_mixed_provider_circuit() -> Circuit:
    circuit = Circuit("e3_mixed_provider_integration")
    x = circuit.input("x")
    recipe = circuit.signals("recipe")
    free_bias = circuit.oracle("free_bias")
    anchored_sensor = circuit.oracle("anchored_sensor")
    ingredients = circuit.oracle_signals("ingredients")
    circuit.bind_oracle_input(ingredients, "recipe", recipe)

    circuit.output("logic", (x + free_bias) * 2 + anchored_sensor)
    circuit.output("ingredients", ingredients)
    return circuit


def compile_mixed_provider_probe():
    return build_mixed_provider_circuit().compile(
        optimize=False,
        placement=PlacementOptions(
            strategy="annealed",
            anchor_io=False,
            iterations=0,
            restarts=1,
        ),
        physical_anchors={"e3-world-sensor": ANCHORED_SENSOR_POSITION},
        oracle_providers={
            "free_bias": ScalarConstantOracleProvider(3),
            "anchored_sensor": ScalarConstantOracleProvider(
                7,
                placement=AnchoredPlacement("e3-world-sensor"),
            ),
            "ingredients": AssemblerIngredientsOracleProvider(),
        },
    )


def generate_mixed_provider_probe_string() -> str:
    return compile_mixed_provider_probe().blueprint_string


def main() -> None:
    print(generate_mixed_provider_probe_string())


if __name__ == "__main__":
    main()
