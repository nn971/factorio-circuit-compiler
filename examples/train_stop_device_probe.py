"""Milestone F4 probe: typed train-stop command/status interface as one rigid provider.

The program leaves ``commands`` as a normal external Level-vector input and observes ``status`` as a
Level-vector oracle.  The provider binds both buses to one reusable train-stop device: GREEN drives
signals sent to the stopped train plus the ``signal-L``/``signal-P`` control lanes. RED carries
train contents plus the ``signal-T`` stopped-train id and ``signal-C`` incoming-train count.
"""

from __future__ import annotations

from dataclasses import dataclass

from factorio_circuit import (
    Circuit,
    OraclePhysicalContext,
    OraclePortDisposition,
    ProviderRigidComponentProduct,
)
from factorio_circuit.devices import (
    TRAIN_STOPPED_SIGNAL,
    TRAINS_COUNT_SIGNAL,
    TrainStopDevice,
)
from factorio_circuit.synthesis import BlueprintEntityPhysicalSpec
from factorio_circuit.synthesis.component_geometry import ComponentRegion
from factorio_circuit.synthesis.placement import PlacementOptions

TRAIN_STOP_ORIGIN = (0.0, 0.0)
TRAIN_STOP_FOOTPRINT = ComponentRegion(0.0, 0.0, 4.0, 3.0)
TRAIN_STOP_PROTOTYPE_SPECS = {
    "constant-combinator": BlueprintEntityPhysicalSpec((0.5, 0.5)),
    "train-stop": BlueprintEntityPhysicalSpec((0.5, 0.5)),
}
_TRAIN_STOP_DEVICE = TrainStopDevice(
    label="F4 train stop interface",
    station="F4 Circuit Interface",
).build()


@dataclass(frozen=True, slots=True)
class TrainStopStatusOracleProvider:
    """Bind command input and expose one train stop's RED status vector as an oracle."""

    def materialize(self, context: OraclePhysicalContext) -> OraclePortDisposition:
        context.add_fixed_signals(TRAIN_STOPPED_SIGNAL, TRAINS_COUNT_SIGNAL)
        context.add_rigid_component(
            ProviderRigidComponentProduct(
                "f4-train-stop",
                _TRAIN_STOP_DEVICE,
                TRAIN_STOP_PROTOTYPE_SPECS,
                origin=TRAIN_STOP_ORIGIN,
                footprints=(TRAIN_STOP_FOOTPRINT,),
                allowed_origins=(TRAIN_STOP_ORIGIN,),
                internal_wire_span=9.0,
                port_bindings=(
                    context.component_input_binding("commands", _TRAIN_STOP_DEVICE, "commands"),
                    context.component_output_binding(_TRAIN_STOP_DEVICE, "status"),
                ),
            )
        )
        return OraclePortDisposition.CONSUMED


def build_train_stop_device_circuit() -> Circuit:
    circuit = Circuit("f4_train_stop_device")
    commands = circuit.signals("commands")
    status = circuit.oracle_signals("status")
    circuit.bind_oracle_input(status, "commands", commands)
    circuit.output("status", status)
    return circuit


def compile_train_stop_device_probe():
    return build_train_stop_device_circuit().compile(
        optimize=False,
        placement=PlacementOptions(
            strategy="annealed",
            anchor_io=False,
            iterations=0,
            restarts=1,
        ),
        oracle_providers={"status": TrainStopStatusOracleProvider()},
    )


def generate_train_stop_device_probe_string() -> str:
    return compile_train_stop_device_probe().blueprint_string


def main() -> None:
    print(generate_train_stop_device_probe_string())


if __name__ == "__main__":
    main()
