"""Provider-aware Event physical lowering.

Stateful Event lowering deliberately exposes only exact ``EventInput`` objects because derived
``EventInput`` subclasses such as ``SumInto`` are compiler-owned logic, not external payload ports.
An ``EventOracleInput`` is the one intentional exception: it is still a true external Event source,
only target-owned rather than manually wired. This thin lowerer preserves the existing exclusion
rule while admitting that explicit oracle subtype.
"""

from __future__ import annotations

from dataclasses import replace

from factorio_circuit.analysis.state_timing import StateTimingPlan
from factorio_circuit.ir.abstract_physical import AbstractPhysicalCircuit
from factorio_circuit.ir.oracle import EventOracleInput
from factorio_circuit.ir.semantic import CircuitModule, EventInput
from factorio_circuit.lowering.clocked_physical import ClockedPhysicalLowerer
from factorio_circuit.lowering.event_accumulator_physical import EventAccumulatorPhysicalLowerer


class ProviderEventPhysicalLowerer(EventAccumulatorPhysicalLowerer):
    """Event accumulator lowerer that also exposes declared Event oracle boundaries."""

    def _create_event_input_markers(self) -> None:
        original = self.module
        external_inputs = tuple(
            source
            for source in original.event_inputs
            if type(source) is EventInput or isinstance(source, EventOracleInput)
        )
        self.module = replace(original, event_inputs=external_inputs)
        try:
            # Bypass StatefulClockedPhysicalLowerer's exact-type filter. We have already applied the
            # same filter above with the single intentional EventOracleInput exception.
            ClockedPhysicalLowerer._create_event_input_markers(self)
        finally:
            self.module = original


def lower_provider_event_physical(
    module: CircuitModule,
    *,
    state_timing: StateTimingPlan,
) -> AbstractPhysicalCircuit:
    return ProviderEventPhysicalLowerer(module, state_timing=state_timing).lower()


__all__ = ["ProviderEventPhysicalLowerer", "lower_provider_event_physical"]
