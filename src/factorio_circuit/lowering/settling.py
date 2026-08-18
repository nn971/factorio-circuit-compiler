"""Validity-aware Level lowering for synchronous settling regions.

The semantic Level IR is clocked by logical occurrences, while Factorio combinators evaluate every
physical tick. A state value held between two clock boundaries therefore does not need to be copied
through identity combinators merely because a consumer is scheduled later in the same interval.

This module implements that fact as a local proof carried by each realized value. A validity window
``[start, end)`` means that the physical net is guaranteed to represent the same logical token for
every tick in that interval. Constants have an unbounded window, state reads are valid for one full
clock period, and raw external Level samples are conservatively valid only at their sampling tick.
Combinational operations intersect the windows of their operands after normal phase selection.

``delay_to`` may therefore reuse a net directly when the requested phase is already inside its
validity window. If the proof is absent, or the token has expired, lowering falls back to the exact
one-tick delay chain used previously. This makes the optimization correctness-preserving for
arbitrary Level circuits while eliminating phase padding inside the common synchronous feedback-cut
case. Intentional temporal delays such as periodic-clock startup are forced through the exact path.
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
    Select,
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
from factorio_circuit.ir.state import VectorRegisterRead
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedValue, RealizedVector
from factorio_circuit.lowering.open_vector import VectorLowerer


@dataclass(frozen=True, slots=True)
class ValidityWindow:
    """Ticks on which one physical representation is the same logical Level token."""

    start: int
    end: int | None

    def __post_init__(self) -> None:
        if self.end is not None and self.end <= self.start:
            raise ValueError("finite validity window must be nonempty")

    @property
    def span(self) -> int | None:
        return None if self.end is None else self.end - self.start

    def contains(self, phase: int) -> bool:
        return phase >= self.start and (self.end is None or phase < self.end)

    def from_phase(self, phase: int) -> ValidityWindow:
        if not self.contains(phase):
            raise ValueError("phase lies outside validity window")
        return ValidityWindow(phase, self.end)

    def shift(self, ticks: int) -> ValidityWindow:
        if ticks < 0:
            raise ValueError("validity windows cannot be shifted backwards")
        return ValidityWindow(
            self.start + ticks,
            None if self.end is None else self.end + ticks,
        )

    def intersect(self, other: ValidityWindow) -> ValidityWindow | None:
        start = max(self.start, other.start)
        if self.end is None:
            end = other.end
        elif other.end is None:
            end = self.end
        else:
            end = min(self.end, other.end)
        if end is not None and end <= start:
            return None
        return ValidityWindow(start, end)


def _end_from_span(start: int, span: int | None) -> int | None:
    return None if span is None else start + span


class SettlingVectorLowerer(VectorLowerer):
    """Production Level lowerer that reuses already-valid values instead of padding phases."""

    def __init__(
        self,
        module: CircuitModule,
        *,
        enable_packing: bool,
        state_timing: StateTimingPlan | None = None,
    ) -> None:
        super().__init__(
            module,
            enable_packing=enable_packing,
            state_timing=state_timing,
        )
        self._scalar_validity: dict[tuple[int, object, int], ValidityWindow] = {}
        self._vector_validity: dict[tuple[int, int], ValidityWindow] = {}
        self._force_exact_alignment = False

    @staticmethod
    def _scalar_key(value: RealizedValue) -> tuple[int, object, int]:
        return (value.net, value.signal, value.phase)

    @staticmethod
    def _vector_key(value: RealizedVector) -> tuple[int, int]:
        return (value.net, value.phase)

    def _remember_scalar(self, value: RealizedValue, window: ValidityWindow) -> None:
        key = self._scalar_key(value)
        existing = self._scalar_validity.get(key)
        if existing is None:
            self._scalar_validity[key] = window
            return
        overlap = existing.intersect(window)
        if overlap is None:
            # The same physical lane/phase reached through two semantic aliases is useful only for
            # ticks proved by both aliases. No common tick means we must stop using persistence
            # information for this key rather than make an unsafe choice later.
            self._scalar_validity.pop(key, None)
        else:
            self._scalar_validity[key] = overlap

    def _remember_vector(self, value: RealizedVector, window: ValidityWindow) -> None:
        key = self._vector_key(value)
        existing = self._vector_validity.get(key)
        if existing is None:
            self._vector_validity[key] = window
            return
        overlap = existing.intersect(window)
        if overlap is None:
            self._vector_validity.pop(key, None)
        else:
            self._vector_validity[key] = overlap

    def _scalar_window(self, value: RealizedValue) -> ValidityWindow | None:
        return self._scalar_validity.get(self._scalar_key(value))

    def _vector_window(self, value: RealizedVector) -> ValidityWindow | None:
        return self._vector_validity.get(self._vector_key(value))

    @staticmethod
    def _point_window(phase: int) -> ValidityWindow:
        return ValidityWindow(phase, phase + 1)

    def _window_after_exact_alignment(
        self,
        window: ValidityWindow,
        source_phase: int,
        target_phase: int,
    ) -> ValidityWindow:
        """Window produced by the legacy exact delay from ``source_phase`` to ``target_phase``."""

        if target_phase < source_phase:
            raise ValueError("cannot align a value backwards")
        shifted = window.shift(target_phase - source_phase)
        if not shifted.contains(target_phase):  # pragma: no cover - start invariant
            raise AssertionError("exact delay failed to transport the certified source tick")
        return shifted.from_phase(target_phase)

    def _aligned_scalar_window(
        self, value: RealizedValue, target_phase: int
    ) -> ValidityWindow:
        window = self._scalar_window(value)
        if window is None:
            return self._point_window(target_phase)
        if window.contains(target_phase):
            return window.from_phase(target_phase)
        return self._window_after_exact_alignment(window, value.phase, target_phase)

    def _aligned_vector_window(
        self, value: RealizedVector, target_phase: int
    ) -> ValidityWindow:
        window = self._vector_window(value)
        if window is None:
            return self._point_window(target_phase)
        if window.contains(target_phase):
            return window.from_phase(target_phase)
        return self._window_after_exact_alignment(window, value.phase, target_phase)

    @staticmethod
    def _combined_span(windows: tuple[ValidityWindow, ...]) -> int | None:
        if not windows:
            return None
        finite = [window.span for window in windows if window.span is not None]
        return None if not finite else min(finite)

    def _scalar_child(self, value: object) -> RealizedValue | None:
        if isinstance(value, Constant):
            return None
        result = self.memo.get(id(value))
        return result if isinstance(result, RealizedValue) else None

    def _vector_child(self, value: object) -> RealizedVector | None:
        if isinstance(value, VectorConstant):
            return None
        result = self.vector_memo.get(id(value))
        return result if isinstance(result, RealizedVector) else None

    def _scalar_operation_window(
        self,
        result: RealizedValue,
        children: tuple[object, ...],
        *,
        target_phase: int | None = None,
    ) -> ValidityWindow:
        realized = tuple(
            child
            for item in children
            if (child := self._scalar_child(item)) is not None
        )
        target = (
            max((child.phase for child in realized), default=0)
            if target_phase is None
            else target_phase
        )
        windows = tuple(self._aligned_scalar_window(child, target) for child in realized)
        span = self._combined_span(windows)
        return ValidityWindow(result.phase, _end_from_span(result.phase, span))

    def _record_scalar_semantics(self, semantic: Value, result: RealizedValue) -> None:
        if isinstance(semantic, Constant):
            window = ValidityWindow(result.phase, None)
        elif isinstance(semantic, (Input, FlowInput, InputSample, FlowInputSample)):
            window = self._point_window(result.phase)
        elif isinstance(semantic, VectorSignal):
            source = self.vector_memo.get(id(semantic.vector))
            source_window = (
                self._vector_window(source) if isinstance(source, RealizedVector) else None
            )
            if source_window is None or not source_window.contains(result.phase):
                window = self._point_window(result.phase)
            else:
                window = source_window.from_phase(result.phase)
        elif isinstance(semantic, BinaryOp):
            target = result.phase - FACTORIO_LATENCY.operation_latency(
                "scalar_binary", semantic.op
            )
            window = self._scalar_operation_window(
                result,
                (semantic.left, semantic.right),
                target_phase=target,
            )
        elif isinstance(semantic, Compare):
            target = result.phase - FACTORIO_LATENCY.operation_latency("compare", semantic.op)
            window = self._scalar_operation_window(
                result,
                (semantic.left, semantic.right),
                target_phase=target,
            )
        elif isinstance(semantic, Select):
            # Generic select lowering may be a one-stage mux or a three-stage arithmetic fallback.
            # Both align semantic children at their maximum pre-select phase. Packed direct-compare
            # selects can skip the materialized condition; using the later semantic condition phase
            # is conservative for validity and never enables an otherwise-unproved reuse.
            window = self._scalar_operation_window(
                result,
                (semantic.condition, semantic.when_true, semantic.when_false),
            )
        else:
            window = self._point_window(result.phase)
        self._remember_scalar(result, window)

    def _record_vector_semantics(self, semantic: VectorValue, result: RealizedVector) -> None:
        if isinstance(semantic, VectorConstant):
            window = ValidityWindow(result.phase, None)
        elif isinstance(
            semantic,
            (VectorInput, FlowVectorInput, VectorInputSample, FlowVectorInputSample),
        ):
            window = self._point_window(result.phase)
        elif isinstance(semantic, VectorRegisterRead):
            timing = self.state_timing.for_read(semantic)
            register_timing = self.state_timing.for_register(semantic.register)
            window = ValidityWindow(
                timing.physical_phase,
                timing.physical_phase + register_timing.period,
            )
        elif isinstance(semantic, VectorBinaryOp):
            left = self._vector_child(semantic.left)
            right = self._vector_child(semantic.right)
            realized = tuple(item for item in (left, right) if item is not None)
            target = result.phase - FACTORIO_LATENCY.operation_latency(
                "vector_binary", semantic.op
            )
            windows = tuple(self._aligned_vector_window(item, target) for item in realized)
            span = self._combined_span(windows)
            window = ValidityWindow(result.phase, _end_from_span(result.phase, span))
        elif isinstance(semantic, VectorScalarOp):
            vector = self._vector_child(semantic.vector)
            scalar = self._scalar_child(semantic.scalar)
            target = result.phase - FACTORIO_LATENCY.operation_latency(
                "vector_scalar", semantic.op
            )
            windows: list[ValidityWindow] = []
            if vector is not None:
                windows.append(self._aligned_vector_window(vector, target))
            if scalar is not None:
                windows.append(self._aligned_scalar_window(scalar, target))
            span = self._combined_span(tuple(windows))
            window = ValidityWindow(result.phase, _end_from_span(result.phase, span))
        elif isinstance(semantic, (VectorFilter, VectorSelect)):
            source = self._vector_child(semantic.vector)
            source_window = self._vector_window(source) if source is not None else None
            if source_window is None:
                if isinstance(semantic.vector, VectorConstant):
                    window = ValidityWindow(result.phase, None)
                else:
                    window = self._point_window(result.phase)
            else:
                window = ValidityWindow(
                    result.phase,
                    _end_from_span(result.phase, source_window.span),
                )
        else:
            window = self._point_window(result.phase)
        self._remember_vector(result, window)

    def _create_input_markers(self) -> None:
        super()._create_input_markers()
        for source in self.module.inputs:
            value = self.memo.get(id(source))
            if isinstance(value, RealizedValue):
                self._remember_scalar(value, self._point_window(value.phase))
        for source in self.module.vector_inputs:
            value = self.vector_memo.get(id(source))
            if isinstance(value, RealizedVector):
                self._remember_vector(value, self._point_window(value.phase))

    def _reserve_state_outputs(self) -> None:
        super()._reserve_state_outputs()
        for register in self.module.state_registers:
            value = self.state_outputs.get(register.name)
            if value is None:
                continue
            timing = self.state_timing.for_register(register)
            self._remember_vector(
                value,
                ValidityWindow(value.phase, value.phase + timing.period),
            )

    def realize(self, value: Value) -> RealizedValue:
        result = super().realize(value)
        self._record_scalar_semantics(value, result)
        return result

    def realize_vector(self, value: VectorValue) -> RealizedVector:
        result = super().realize_vector(value)
        self._record_vector_semantics(value, result)
        return result

    def _emit_binary_from_operands(
        self,
        operation: str,
        left: RealizedValue | int,
        right: RealizedValue | int,
        *,
        description: str | None = None,
    ) -> RealizedValue:
        target = max(
            (item.phase for item in (left, right) if isinstance(item, RealizedValue)),
            default=0,
        )
        windows: list[ValidityWindow] = []
        if isinstance(left, RealizedValue):
            windows.append(self._aligned_scalar_window(left, target))
        if isinstance(right, RealizedValue):
            windows.append(self._aligned_scalar_window(right, target))

        result = super()._emit_binary_from_operands(
            operation,
            left,
            right,
            description=description,
        )
        span = self._combined_span(tuple(windows))
        self._remember_scalar(
            result,
            ValidityWindow(result.phase, _end_from_span(result.phase, span)),
        )
        return result

    def delay_to(self, value: RealizedValue, target_phase: int) -> RealizedValue:
        if value.phase > target_phase:
            raise ValueError("cannot delay backwards in time")
        if value.phase == target_phase:
            return value

        window = self._scalar_window(value)
        if not self._force_exact_alignment and window is not None and window.contains(target_phase):
            result = RealizedValue(
                signal=value.signal,
                net=value.net,
                phase=target_phase,
                clean_single_lane=value.clean_single_lane,
            )
            self._remember_scalar(result, window.from_phase(target_phase))
            return result

        result = super().delay_to(value, target_phase)
        if window is not None:
            self._remember_scalar(
                result,
                self._window_after_exact_alignment(window, value.phase, target_phase),
            )
        return result

    def delay_vector_to(self, value: RealizedVector, target_phase: int) -> RealizedVector:
        if value.phase > target_phase:
            raise ValueError("cannot delay vector backwards in time")
        if value.phase == target_phase:
            return value

        window = self._vector_window(value)
        if not self._force_exact_alignment and window is not None and window.contains(target_phase):
            result = RealizedVector(value.net, target_phase)
            self._remember_vector(result, window.from_phase(target_phase))
            return result

        result = super().delay_vector_to(value, target_phase)
        if window is not None:
            self._remember_vector(
                result,
                self._window_after_exact_alignment(window, value.phase, target_phase),
            )
        return result

    def _startup_ready(self, target_phase: int) -> RealizedValue:
        """Preserve startup as a real temporal delay rather than a Level-validity reuse."""

        previous = self._force_exact_alignment
        self._force_exact_alignment = True
        try:
            return super()._startup_ready(target_phase)
        finally:
            self._force_exact_alignment = previous
