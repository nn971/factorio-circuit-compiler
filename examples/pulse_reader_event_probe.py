"""Milestone F3 probe: native Factorio item pulses as compiler Event providers.

The deterministic program accumulates one vector Event oracle. The target provider is either a real
transport-belt pulse reader or a real inserter pulse reader. Each rigid device binds an aligned RED
Event payload and GREEN ``signal-V`` valid token directly to the physical Event ABI produced by
clocked lowering; no external payload/valid markers survive the final blueprint.
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
    ExternalDeviceBlueprint,
    InserterPulseReaderDevice,
    TransportBeltPulseReaderDevice,
)
from factorio_circuit.event_oracles import EventOraclePhysicalContext
from factorio_circuit.synthesis import BlueprintConnectorShape, BlueprintEntityPhysicalSpec
from factorio_circuit.synthesis.component_geometry import ComponentRegion
from factorio_circuit.synthesis.placement import PlacementOptions

PULSE_READER_ORIGIN = (0.0, 0.0)
PULSE_READER_FOOTPRINT = ComponentRegion(0.0, 0.0, 5.0, 3.0)


def _prototype_specs(reader_prototype: str) -> dict[str, BlueprintEntityPhysicalSpec]:
    return {
        "constant-combinator": BlueprintEntityPhysicalSpec((0.5, 0.5)),
        "arithmetic-combinator": BlueprintEntityPhysicalSpec(
            (1.0, 0.5),
            BlueprintConnectorShape.INPUT_OUTPUT,
        ),
        "decider-combinator": BlueprintEntityPhysicalSpec(
            (1.0, 0.5),
            BlueprintConnectorShape.INPUT_OUTPUT,
        ),
        reader_prototype: BlueprintEntityPhysicalSpec((0.5, 0.5)),
    }


@dataclass(frozen=True, slots=True)
class PulseReaderEventOracleProvider:
    """Bind one F3 pulse-reader device to one vector Event oracle."""

    name: str
    device: ExternalDeviceBlueprint
    reader_prototype: str

    def materialize(self, context: OraclePhysicalContext) -> OraclePortDisposition:
        if not isinstance(context, EventOraclePhysicalContext):
            raise ValueError("pulse reader provider requires an Event oracle context")
        bindings = context.component_event_output_bindings(self.device, "items", "valid")
        context.add_rigid_component(
            ProviderRigidComponentProduct(
                self.name,
                self.device,
                _prototype_specs(self.reader_prototype),
                origin=PULSE_READER_ORIGIN,
                footprints=(PULSE_READER_FOOTPRINT,),
                allowed_origins=(PULSE_READER_ORIGIN,),
                internal_wire_span=7.0,
                port_bindings=bindings,
            )
        )
        return OraclePortDisposition.CONSUMED


def build_pulse_reader_event_circuit() -> Circuit:
    circuit = Circuit("f3_pulse_reader_event")
    items = circuit.oracle_signal_event("items", guaranteed_min_separation=1)
    total = circuit.accumulator("total")
    total.add(items + circuit.constant_signals({}))
    circuit.output("total", total.sample())
    return circuit


def compile_transport_belt_pulse_reader_probe():
    device = TransportBeltPulseReaderDevice(label="F3 belt Event reader").build()
    return build_pulse_reader_event_circuit().compile(
        optimize=False,
        placement=PlacementOptions(
            strategy="annealed",
            anchor_io=False,
            iterations=0,
            restarts=1,
        ),
        oracle_providers={
            "items": PulseReaderEventOracleProvider(
                "f3-belt-reader",
                device,
                "transport-belt",
            )
        },
    )


def compile_inserter_pulse_reader_probe():
    device = InserterPulseReaderDevice(label="F3 inserter Event reader").build()
    return build_pulse_reader_event_circuit().compile(
        optimize=False,
        placement=PlacementOptions(
            strategy="annealed",
            anchor_io=False,
            iterations=0,
            restarts=1,
        ),
        oracle_providers={
            "items": PulseReaderEventOracleProvider(
                "f3-inserter-reader",
                device,
                "inserter",
            )
        },
    )


def main() -> None:
    print(compile_transport_belt_pulse_reader_probe().blueprint_string)


if __name__ == "__main__":
    main()
