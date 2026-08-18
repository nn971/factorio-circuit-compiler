"""Experimental Level lowering that bypasses sub-period phase-alignment delays.

This is a probe, not a production lowering.  Ordinary scalar/vector alignment requests shorter than
one state-clock period are treated as if the logical Level value remains directly usable: no delay
combinators are emitted, but the returned realization is annotated with the requested later phase so
subsequent lowering can proceed.

The deliberate startup delay used by the periodic state clock is preserved.  Alignment requests that
span a whole period or more are also preserved.  Therefore this experiment tests the useful core
hypothesis -- that most within-occurrence Level alignment is unnecessary -- without erasing timing
hardware whose purpose is not merely operand alignment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from factorio_circuit.analysis.state_timing import StateTimingPlan
from factorio_circuit.ir.abstract_physical import AbstractNet, AbstractPhysicalCircuit
from factorio_circuit.ir.semantic import (
    CircuitModule,
    ScalarValue,
    VectorBinaryOp,
    VectorConstant,
    VectorFilter,
    VectorInput,
    VectorInputSample,
    VectorScalarOp,
    VectorSelect,
)
from factorio_circuit.ir.state import VectorRegisterRead
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedValue, RealizedVector
from factorio_circuit.lowering.open_vector import VectorLowerer

_VECTOR_OUTPUTS = (
    VectorInput,
    VectorInputSample,
    VectorConstant,
    VectorRegisterRead,
    VectorBinaryOp,
    VectorScalarOp,
    VectorFilter,
    VectorSelect,
)


@dataclass(frozen=True, slots=True)
class DelayBypassStats:
    period: int
    scalar_alignment_calls_bypassed: int
    scalar_alignment_ticks_bypassed: int
    vector_alignment_calls_bypassed: int
    vector_alignment_ticks_bypassed: int
    scalar_alignment_calls_preserved: int
    scalar_alignment_ticks_preserved: int
    vector_alignment_calls_preserved: int
    vector_alignment_ticks_preserved: int
    startup_delay_ticks_preserved: int


class DelayBypassVectorLowerer(VectorLowerer):
    """Vector lowerer that treats within-period Level alignment as zero hardware cost."""

    def __init__(
        self,
        module: CircuitModule,
        *,
        enable_packing: bool,
        state_timing: StateTimingPlan,
    ) -> None:
        super().__init__(module, enable_packing=enable_packing, state_timing=state_timing)
        period = state_timing.uniform_period
        if period is None or period < 2:
            raise ValueError("delay-bypass experiment requires one uniform multicycle state period")
        self._bypass_period = period
        self._preserve_alignment = False
        self._scalar_calls_bypassed = 0
        self._scalar_ticks_bypassed = 0
        self._vector_calls_bypassed = 0
        self._vector_ticks_bypassed = 0
        self._scalar_calls_preserved = 0
        self._scalar_ticks_preserved = 0
        self._vector_calls_preserved = 0
        self._vector_ticks_preserved = 0
        self._startup_ticks_preserved = 0

    def _startup_ready(self, target_phase: int) -> RealizedValue:
        # Startup is an intentional temporal transition, not an operand-alignment convenience.
        before = 0 if self._startup_source is None else self._startup_source.phase
        self._preserve_alignment = True
        try:
            result = super()._startup_ready(target_phase)
        finally:
            self._preserve_alignment = False
        self._startup_ticks_preserved = max(self._startup_ticks_preserved, target_phase - before)
        return result

    def delay_to(self, value: RealizedValue, target_phase: int) -> RealizedValue:
        if value.phase > target_phase:
            raise ValueError("cannot delay backwards in time")
        delta = target_phase - value.phase
        if delta == 0:
            return value
        if self._preserve_alignment or delta >= self._bypass_period:
            self._scalar_calls_preserved += 1
            self._scalar_ticks_preserved += delta
            return super().delay_to(value, target_phase)
        self._scalar_calls_bypassed += 1
        self._scalar_ticks_bypassed += delta
        return RealizedValue(
            signal=value.signal,
            net=value.net,
            phase=target_phase,
            clean_single_lane=value.clean_single_lane,
        )

    def delay_vector_to(self, value: RealizedVector, target_phase: int) -> RealizedVector:
        if value.phase > target_phase:
            raise ValueError("cannot delay vector backwards in time")
        delta = target_phase - value.phase
        if delta == 0:
            return value
        if delta >= self._bypass_period:
            self._vector_calls_preserved += 1
            self._vector_ticks_preserved += delta
            return super().delay_vector_to(value, target_phase)
        self._vector_calls_bypassed += 1
        self._vector_ticks_bypassed += delta
        return RealizedVector(value.net, target_phase)

    def stats(self) -> DelayBypassStats:
        return DelayBypassStats(
            period=self._bypass_period,
            scalar_alignment_calls_bypassed=self._scalar_calls_bypassed,
            scalar_alignment_ticks_bypassed=self._scalar_ticks_bypassed,
            vector_alignment_calls_bypassed=self._vector_calls_bypassed,
            vector_alignment_ticks_bypassed=self._vector_ticks_bypassed,
            scalar_alignment_calls_preserved=self._scalar_calls_preserved,
            scalar_alignment_ticks_preserved=self._scalar_ticks_preserved,
            vector_alignment_calls_preserved=self._vector_calls_preserved,
            vector_alignment_ticks_preserved=self._vector_ticks_preserved,
            startup_delay_ticks_preserved=self._startup_ticks_preserved,
        )


def lower_with_delay_bypass(
    module: CircuitModule,
    *,
    state_timing: StateTimingPlan,
    enable_packing: bool = False,
) -> tuple[AbstractPhysicalCircuit, DelayBypassStats]:
    """Lower one canonical Level module using the experimental within-period bypass rule."""

    lowerer = DelayBypassVectorLowerer(
        module,
        enable_packing=enable_packing,
        state_timing=state_timing,
    )
    lowerer._create_input_markers()
    if module.state_registers:
        lowerer._reserve_state_outputs()
        lowerer._create_state_components()

    outputs: list[RealizedValue | RealizedVector] = []
    for value in module.output.values:
        if isinstance(value, _VECTOR_OUTPUTS):
            outputs.append(lowerer.realize_vector(value))
        else:
            outputs.append(lowerer.realize(cast(ScalarValue, value)))
    lowerer._create_output_markers(outputs)
    lowerer.circuit.nets = [
        AbstractNet(
            id=net_id,
            signals=builder.signals,
            endpoints=tuple(builder.endpoints),
            label=builder.label,
            fixed_signals=builder.fixed_signals,
            carries_dynamic_vector=builder.carries_dynamic_vector,
        )
        for net_id, builder in sorted(lowerer.net_builders.items())
    ]
    lowerer.circuit.validate()
    return lowerer.circuit, lowerer.stats()
