"""Sampling-policy-aware periodic Level lowering.

The ordinary ALAP lowerer treats a phase-zero external input as an exact snapshot. When a later
consumer needs that token it therefore inserts identity combinators to transport the snapshot to the
consumer phase.

Under :class:`SamplingPolicy.ALAP`, external Level inputs and oracles are instead live observation
boundaries. A request to align a *phase-zero* external source to a later phase means "observe the
same live circuit-network source at that phase" rather than "delay the old sample". No physical
entity is needed for that relocation. Explicit logical samples/reindexing already have a nonzero
phase and deliberately keep exact-transport behavior.

The Level-alignment parent owns validity reuse and exact token transport. This layer adds only one
new temporal operation: late observation of a live external source. Event/pulse transport and
stateful HOLD remain separate concerns and must not be folded into ``delay_to``.
"""

from __future__ import annotations

from factorio_circuit.analysis.state_timing import StateTimingPlan
from factorio_circuit.ir.semantic import CircuitModule
from factorio_circuit.lowering.alap import AlapVectorLowerer
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedValue, RealizedVector
from factorio_circuit.sampling import SamplingPolicy


class SamplingPolicyLowerer(AlapVectorLowerer):
    """ALAP Level lowerer that adds explicit late observation of live external sources."""

    def __init__(
        self,
        module: CircuitModule,
        *,
        enable_packing: bool,
        state_timing: StateTimingPlan | None = None,
        sampling_policy: SamplingPolicy = SamplingPolicy.BEGINNING_OF_STEP,
    ) -> None:
        if not isinstance(sampling_policy, SamplingPolicy):
            raise TypeError("sampling_policy must be a SamplingPolicy")
        super().__init__(
            module,
            enable_packing=enable_packing,
            state_timing=state_timing,
        )
        self.sampling_policy = sampling_policy
        self._external_scalar_sources: set[tuple[int, object]] = set()
        self._external_vector_nets: set[int] = set()

    def _create_input_markers(self) -> None:
        super()._create_input_markers()
        for source in self.module.inputs:
            realized = self.memo.get(id(source))
            if isinstance(realized, RealizedValue):
                self._external_scalar_sources.add((realized.net, realized.signal))
        for source in self.module.vector_inputs:
            realized = self.vector_memo.get(id(source))
            if isinstance(realized, RealizedVector):
                self._external_vector_nets.add(realized.net)

    def _can_resample_scalar(self, value: RealizedValue) -> bool:
        return (
            self.sampling_policy is SamplingPolicy.ALAP
            and value.phase == 0
            and (
                (value.net, value.signal) in self._external_scalar_sources
                or value.net in self._external_vector_nets
            )
        )

    def _can_resample_vector(self, value: RealizedVector) -> bool:
        return (
            self.sampling_policy is SamplingPolicy.ALAP
            and value.phase == 0
            and value.net in self._external_vector_nets
        )

    def observe_scalar_at(self, value: RealizedValue, target_phase: int) -> RealizedValue:
        """Observe the same live scalar wire at ``target_phase`` without preserving an old token."""

        if value.phase > target_phase:
            raise ValueError("cannot observe a scalar backwards in time")
        result = RealizedValue(
            signal=value.signal,
            net=value.net,
            phase=target_phase,
            clean_single_lane=value.clean_single_lane,
        )
        self._remember_scalar(result, self._point_window(target_phase))
        return result

    def observe_vector_at(self, value: RealizedVector, target_phase: int) -> RealizedVector:
        """Observe the same live vector wire at ``target_phase`` without preserving an old token."""

        if value.phase > target_phase:
            raise ValueError("cannot observe a vector backwards in time")
        result = RealizedVector(value.net, target_phase)
        self._remember_vector(result, self._point_window(target_phase))
        return result

    def delay_to(self, value: RealizedValue, target_phase: int) -> RealizedValue:
        """Align a Level scalar, allowing a late live observation when policy permits it."""

        if value.phase > target_phase:
            raise ValueError("cannot delay backwards in time")
        if value.phase == target_phase:
            return value
        if self._can_resample_scalar(value):
            return self.observe_scalar_at(value, target_phase)
        return super().delay_to(value, target_phase)

    def delay_vector_to(self, value: RealizedVector, target_phase: int) -> RealizedVector:
        """Align a Level vector, allowing a late live observation when policy permits it."""

        if value.phase > target_phase:
            raise ValueError("cannot delay vector backwards in time")
        if value.phase == target_phase:
            return value
        if self._can_resample_vector(value):
            return self.observe_vector_at(value, target_phase)
        return super().delay_vector_to(value, target_phase)


__all__ = ["SamplingPolicyLowerer"]
