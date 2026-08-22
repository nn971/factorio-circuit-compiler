"""Sampling-policy-aware periodic Level lowering.

``SamplingPolicy.ALAP`` is a freshness relaxation for Level values. A physical representation that
is known to keep tracking a live Level may be observed at a later phase and intentionally denote the
later value. This is distinct from exact transport, which preserves one already-chosen token.

The important consequence is that re-observability propagates through ordinary feed-forward Level
logic. Factorio combinators evaluate continuously, so a comparison or arithmetic result whose
non-constant inputs are all still observable also remains observable at later phases. Once an exact
sample/transport enters a cone, that freshness proof naturally collapses to the exact point carried
by the ordinary validity analysis.

The Level-alignment parent continues to own same-token validity reuse and exact token transport.
This layer adds only the separate proof that a physical Level representation may intentionally be
re-observed later. Event/pulse transport and stateful HOLD remain separate concerns.
"""

from __future__ import annotations

from dataclasses import dataclass

from factorio_circuit.analysis.latency import FACTORIO_LATENCY
from factorio_circuit.analysis.state_timing import StateTimingPlan
from factorio_circuit.ir.semantic import (
    BinaryOp,
    CircuitModule,
    Compare,
    Constant,
    FlowInput,
    FlowInputSample,
    FlowVectorInput,
    FlowVectorInputSample,
    Input,
    InputSample,
    Value,
    VectorBinaryOp,
    VectorConstant,
    VectorFilter,
    VectorInput,
    VectorInputSample,
    VectorScalarOp,
    VectorSelect,
    VectorSignal,
    VectorValue,
)
from factorio_circuit.lowering.alap import AlapVectorLowerer
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedValue, RealizedVector
from factorio_circuit.sampling import SamplingPolicy


@dataclass(frozen=True, slots=True)
class ObservationWindow:
    """Ticks on which one physical Level representation may intentionally be re-observed."""

    start: int
    end: int | None

    def __post_init__(self) -> None:
        if self.end is not None and self.end <= self.start:
            raise ValueError("finite observation window must be nonempty")

    @property
    def span(self) -> int | None:
        return None if self.end is None else self.end - self.start

    def contains(self, phase: int) -> bool:
        return phase >= self.start and (self.end is None or phase < self.end)

    def from_phase(self, phase: int) -> ObservationWindow:
        if not self.contains(phase):
            raise ValueError("phase lies outside observation window")
        return ObservationWindow(phase, self.end)

    def intersect(self, other: ObservationWindow) -> ObservationWindow | None:
        start = max(self.start, other.start)
        if self.end is None:
            end = other.end
        elif other.end is None:
            end = self.end
        else:
            end = min(self.end, other.end)
        if end is not None and end <= start:
            return None
        return ObservationWindow(start, end)


class SamplingPolicyLowerer(AlapVectorLowerer):
    """ALAP Level lowerer with explicit late observation of live/re-observable values."""

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
        self._scalar_observability: dict[tuple[int, object, int], ObservationWindow] = {}
        self._vector_observability: dict[tuple[int, int], ObservationWindow] = {}

    def _remember_scalar_observability(
        self,
        value: RealizedValue,
        window: ObservationWindow,
    ) -> None:
        key = self._scalar_key(value)
        existing = self._scalar_observability.get(key)
        if existing is None:
            self._scalar_observability[key] = window
            return
        overlap = existing.intersect(window)
        if overlap is None:
            self._scalar_observability.pop(key, None)
        else:
            self._scalar_observability[key] = overlap

    def _remember_vector_observability(
        self,
        value: RealizedVector,
        window: ObservationWindow,
    ) -> None:
        key = self._vector_key(value)
        existing = self._vector_observability.get(key)
        if existing is None:
            self._vector_observability[key] = window
            return
        overlap = existing.intersect(window)
        if overlap is None:
            self._vector_observability.pop(key, None)
        else:
            self._vector_observability[key] = overlap

    def _scalar_observation_window(self, value: RealizedValue) -> ObservationWindow | None:
        return self._scalar_observability.get(self._scalar_key(value))

    def _vector_observation_window(self, value: RealizedVector) -> ObservationWindow | None:
        return self._vector_observability.get(self._vector_key(value))

    def _scalar_available_window(self, value: RealizedValue) -> ObservationWindow | None:
        live = self._scalar_observation_window(value)
        if live is not None:
            return live
        stable = self._scalar_window(value)
        if stable is None:
            return None
        return ObservationWindow(stable.start, stable.end)

    def _vector_available_window(self, value: RealizedVector) -> ObservationWindow | None:
        live = self._vector_observation_window(value)
        if live is not None:
            return live
        stable = self._vector_window(value)
        if stable is None:
            return None
        return ObservationWindow(stable.start, stable.end)

    @staticmethod
    def _derived_window(
        result_phase: int,
        target_phase: int,
        windows: tuple[ObservationWindow, ...],
    ) -> ObservationWindow | None:
        if not windows or any(not window.contains(target_phase) for window in windows):
            return None
        finite_ends = [window.end for window in windows if window.end is not None]
        end = min(finite_ends) if finite_ends else None
        if end is None:
            return ObservationWindow(result_phase, None)
        span = end - target_phase
        if span <= 0:
            return None
        return ObservationWindow(result_phase, result_phase + span)

    def _scalar_children_observation_window(
        self,
        result: RealizedValue,
        children: tuple[object, ...],
        *,
        target_phase: int,
    ) -> ObservationWindow | None:
        realized: list[RealizedValue] = []
        has_live = False
        for semantic in children:
            if isinstance(semantic, Constant):
                continue
            child = self.memo.get(id(semantic))
            if not isinstance(child, RealizedValue):
                return None
            realized.append(child)
            has_live = has_live or self._scalar_observation_window(child) is not None
        if not has_live:
            return None
        windows = tuple(
            window
            for child in realized
            if (window := self._scalar_available_window(child)) is not None
        )
        if len(windows) != len(realized):
            return None
        return self._derived_window(result.phase, target_phase, windows)

    def _record_scalar_semantics(self, semantic: Value, result: RealizedValue) -> None:
        super()._record_scalar_semantics(semantic, result)
        if self.sampling_policy is not SamplingPolicy.ALAP:
            return

        window: ObservationWindow | None = None
        if (
            isinstance(semantic, (Input, FlowInput))
            or isinstance(semantic, (InputSample, FlowInputSample))
            and semantic.offset == 0
        ):
            window = ObservationWindow(result.phase, None)
        elif isinstance(semantic, VectorSignal):
            vector = self.vector_memo.get(id(semantic.vector))
            if isinstance(vector, RealizedVector):
                source_window = self._vector_observation_window(vector)
                if source_window is not None and source_window.contains(result.phase):
                    window = source_window.from_phase(result.phase)
        elif isinstance(semantic, BinaryOp):
            target = result.phase - FACTORIO_LATENCY.operation_latency("scalar_binary", semantic.op)
            window = self._scalar_children_observation_window(
                result,
                (semantic.left, semantic.right),
                target_phase=target,
            )
        elif isinstance(semantic, Compare):
            target = result.phase - FACTORIO_LATENCY.operation_latency("compare", semantic.op)
            window = self._scalar_children_observation_window(
                result,
                (semantic.left, semantic.right),
                target_phase=target,
            )

        if window is not None:
            self._remember_scalar_observability(result, window)

    def _vector_children_observation_window(
        self,
        result: RealizedVector,
        vectors: tuple[VectorValue, ...],
        scalars: tuple[Value, ...] = (),
        *,
        target_phase: int,
    ) -> ObservationWindow | None:
        windows: list[ObservationWindow] = []
        has_live = False
        for semantic in vectors:
            if isinstance(semantic, VectorConstant):
                continue
            child = self.vector_memo.get(id(semantic))
            if not isinstance(child, RealizedVector):
                return None
            has_live = has_live or self._vector_observation_window(child) is not None
            window = self._vector_available_window(child)
            if window is None:
                return None
            windows.append(window)
        for scalar_semantic in scalars:
            if isinstance(scalar_semantic, Constant):
                continue
            scalar_child = self.memo.get(id(scalar_semantic))
            if not isinstance(scalar_child, RealizedValue):
                return None
            has_live = has_live or self._scalar_observation_window(scalar_child) is not None
            window = self._scalar_available_window(scalar_child)
            if window is None:
                return None
            windows.append(window)
        if not has_live:
            return None
        return self._derived_window(result.phase, target_phase, tuple(windows))

    def _record_vector_semantics(self, semantic: VectorValue, result: RealizedVector) -> None:
        super()._record_vector_semantics(semantic, result)
        if self.sampling_policy is not SamplingPolicy.ALAP:
            return

        window: ObservationWindow | None = None
        if (
            isinstance(semantic, (VectorInput, FlowVectorInput))
            or isinstance(semantic, (VectorInputSample, FlowVectorInputSample))
            and semantic.offset == 0
        ):
            window = ObservationWindow(result.phase, None)
        elif isinstance(semantic, VectorBinaryOp):
            target = result.phase - FACTORIO_LATENCY.operation_latency("vector_binary", semantic.op)
            window = self._vector_children_observation_window(
                result,
                (semantic.left, semantic.right),
                target_phase=target,
            )
        elif isinstance(semantic, VectorScalarOp):
            target = result.phase - FACTORIO_LATENCY.operation_latency("vector_scalar", semantic.op)
            window = self._vector_children_observation_window(
                result,
                (semantic.vector,),
                (semantic.scalar,),
                target_phase=target,
            )
        elif isinstance(semantic, (VectorFilter, VectorSelect)):
            family = "vector_select" if isinstance(semantic, VectorSelect) else "vector_filter"
            target = result.phase - FACTORIO_LATENCY.operation_latency(family, semantic.op)
            window = self._vector_children_observation_window(
                result,
                (semantic.vector,),
                target_phase=target,
            )

        if window is not None:
            self._remember_vector_observability(result, window)

    def _create_input_markers(self) -> None:
        super()._create_input_markers()
        for source in self.module.inputs:
            scalar_realized = self.memo.get(id(source))
            if isinstance(scalar_realized, RealizedValue):
                self._external_scalar_sources.add((scalar_realized.net, scalar_realized.signal))
                if self.sampling_policy is SamplingPolicy.ALAP:
                    self._remember_scalar_observability(
                        scalar_realized, ObservationWindow(scalar_realized.phase, None)
                    )
        for vector_source in self.module.vector_inputs:
            vector_realized = self.vector_memo.get(id(vector_source))
            if isinstance(vector_realized, RealizedVector):
                self._external_vector_nets.add(vector_realized.net)
                if self.sampling_policy is SamplingPolicy.ALAP:
                    self._remember_vector_observability(
                        vector_realized, ObservationWindow(vector_realized.phase, None)
                    )

    def _can_resample_scalar(self, value: RealizedValue) -> bool:
        """Legacy raw-source hook used by the experimental temporal-plan lowerer."""

        return (
            self.sampling_policy is SamplingPolicy.ALAP
            and (value.net, value.signal) in self._external_scalar_sources
        )

    def _can_resample_vector(self, value: RealizedVector) -> bool:
        """Legacy raw-source hook; general re-observation uses the observation-window proof."""

        return (
            self.sampling_policy is SamplingPolicy.ALAP and value.net in self._external_vector_nets
        )

    def _can_observe_scalar_at(self, value: RealizedValue, target_phase: int) -> bool:
        window = self._scalar_observation_window(value)
        return window is not None and window.contains(target_phase)

    def _can_observe_vector_at(self, value: RealizedVector, target_phase: int) -> bool:
        window = self._vector_observation_window(value)
        return window is not None and window.contains(target_phase)

    def observe_scalar_at(self, value: RealizedValue, target_phase: int) -> RealizedValue:
        """Observe the same physical Level lane later, intentionally selecting the later value."""

        if value.phase > target_phase:
            raise ValueError("cannot observe a scalar backwards in time")
        result = RealizedValue(
            signal=value.signal,
            net=value.net,
            phase=target_phase,
            clean_single_lane=value.clean_single_lane,
        )
        self._remember_scalar(result, self._point_window(target_phase))
        window = self._scalar_observation_window(value)
        if window is not None and window.contains(target_phase):
            self._remember_scalar_observability(result, window.from_phase(target_phase))
        return result

    def observe_vector_at(self, value: RealizedVector, target_phase: int) -> RealizedVector:
        """Observe the same physical Level vector later, intentionally selecting the later value."""

        if value.phase > target_phase:
            raise ValueError("cannot observe a vector backwards in time")
        result = RealizedVector(value.net, target_phase)
        self._remember_vector(result, self._point_window(target_phase))
        window = self._vector_observation_window(value)
        if window is not None and window.contains(target_phase):
            self._remember_vector_observability(result, window.from_phase(target_phase))
        return result

    def delay_to(self, value: RealizedValue, target_phase: int) -> RealizedValue:
        """Align a Level scalar by stable reuse, fresh observation, or exact transport."""

        if value.phase > target_phase:
            raise ValueError("cannot delay backwards in time")
        if value.phase == target_phase:
            return value
        stable = self._scalar_window(value)
        if stable is not None and stable.contains(target_phase):
            return super().delay_to(value, target_phase)
        if self._can_observe_scalar_at(value, target_phase):
            return self.observe_scalar_at(value, target_phase)
        return super().delay_to(value, target_phase)

    def delay_vector_to(self, value: RealizedVector, target_phase: int) -> RealizedVector:
        """Align a Level vector by stable reuse, fresh observation, or exact transport."""

        if value.phase > target_phase:
            raise ValueError("cannot delay vector backwards in time")
        if value.phase == target_phase:
            return value
        stable = self._vector_window(value)
        if stable is not None and stable.contains(target_phase):
            return super().delay_vector_to(value, target_phase)
        if self._can_observe_vector_at(value, target_phase):
            return self.observe_vector_at(value, target_phase)
        return super().delay_vector_to(value, target_phase)


__all__ = ["ObservationWindow", "SamplingPolicyLowerer"]
