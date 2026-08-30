"""F2 probe: realize a Level-vector stock oracle with the reusable roboport reader."""

from __future__ import annotations

from dataclasses import dataclass

from factorio_circuit import (
    Circuit,
    OraclePhysicalContext,
    OraclePortDisposition,
    ProviderRigidComponentProduct,
)
from factorio_circuit.devices import RoboportStockReaderDevice
from factorio_circuit.synthesis import BlueprintEntityPhysicalSpec
from factorio_circuit.synthesis.component_geometry import ComponentRegion
from factorio_circuit.synthesis.placement import PlacementOptions

ROBOPORT_ORIGIN = (0.0, 0.0)
ROBOPORT_PROTOTYPE_SPECS = {
    "constant-combinator": BlueprintEntityPhysicalSpec((0.5, 0.5)),
    "roboport": BlueprintEntityPhysicalSpec((2.0, 2.0)),
}
_ROBOPORT_DEVICE = RoboportStockReaderDevice(label="F2 logistic stock reader").build()


@dataclass(frozen=True, slots=True)
class RoboportStockOracleProvider:
    """Expose the roboport's logistic-network item vector as one physical oracle."""

    def materialize(self, context: OraclePhysicalContext) -> OraclePortDisposition:
        context.add_rigid_component(
            ProviderRigidComponentProduct(
                "f2-roboport-stock-reader",
                _ROBOPORT_DEVICE,
                ROBOPORT_PROTOTYPE_SPECS,
                origin=ROBOPORT_ORIGIN,
                footprints=(ComponentRegion(0.0, 0.0, 5.0, 4.0),),
                allowed_origins=(ROBOPORT_ORIGIN,),
                internal_wire_span=9.0,
                port_bindings=(context.component_output_binding(_ROBOPORT_DEVICE, "stock"),),
            )
        )
        return OraclePortDisposition.CONSUMED


def build_roboport_stock_reader_circuit() -> Circuit:
    circuit = Circuit("f2_roboport_stock_reader")
    stock = circuit.oracle_signals("stock")
    circuit.output("stock", stock)
    return circuit


def compile_roboport_stock_reader_probe():
    return build_roboport_stock_reader_circuit().compile(
        optimize=False,
        placement=PlacementOptions(
            strategy="annealed",
            anchor_io=False,
            iterations=0,
            restarts=1,
        ),
        oracle_providers={"stock": RoboportStockOracleProvider()},
    )


def generate_roboport_stock_reader_probe_string() -> str:
    return compile_roboport_stock_reader_probe().blueprint_string


def main() -> None:
    print(generate_roboport_stock_reader_probe_string())


if __name__ == "__main__":
    main()
