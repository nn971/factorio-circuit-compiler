"""Whole-vector physical synthesis extension."""

from dataclasses import replace
from typing import Any

from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import DeciderCombinator, SignalId, WireColor
from factorio_circuit.lowering.vector_unary import VECTOR_EACH_PLACEHOLDER
from factorio_circuit.synthesis.layout import Layout
from factorio_circuit.synthesis.physical import PhysicalSynthesizer
from factorio_circuit.synthesis.placement import PlacementOptions


class VectorPhysicalSynthesizer(PhysicalSynthesizer):
    def _materialize_entity(
        self,
        entity: abstract.AbstractEntity,
        signals: dict[int, SignalId],
        net_colors: dict[int, WireColor],
        annotation_descriptions: dict[int, str],
    ) -> Any:
        result = super()._materialize_entity(
            entity,
            signals,
            net_colors,
            annotation_descriptions,
        )
        if isinstance(entity, abstract.DeciderCombinator) and isinstance(entity.output_signal, int):
            output = self.circuit.signal_by_id(entity.output_signal)
            if output.label == VECTOR_EACH_PLACEHOLDER:
                assert isinstance(result, DeciderCombinator)
                return replace(result, output_signal=SignalId("virtual", "signal-each"))
        return result


def synthesize_vector_layout(
    circuit: abstract.AbstractPhysicalCircuit,
    *,
    safe_wire_span: float,
    placement: PlacementOptions | None = None,
) -> Layout:
    return VectorPhysicalSynthesizer(
        circuit,
        safe_wire_span=safe_wire_span,
        placement_options=placement,
    ).synthesize()
