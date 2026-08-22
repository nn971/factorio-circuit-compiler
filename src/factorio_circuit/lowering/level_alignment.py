"""Explicit temporal alignment operations for periodic Level lowering.

This layer separates three meanings that historically accumulated behind ``delay_to``:

* re-observe/reuse a physical Level representation that is already proved valid later;
* physically transport one *exact* already-chosen token through identity combinators; and
* force startup timing through exact transport even though the startup source is a constant Level.

Live external observation is deliberately not implemented here; :mod:`input_sampling` adds that
policy on top. Event/pulse transport and stateful pulse HOLD are also deliberately outside this
Level-only layer. They require their own temporal operations once Event physical lowering is added.

``delay_to`` remains a compatibility entry point for existing Level lowering call sites, but it is
now a small policy dispatcher rather than the primitive used when exact transport is required.
Callers that mean exact token preservation must use :meth:`exact_delay_to` or
:meth:`exact_delay_vector_to` explicitly.
"""

from __future__ import annotations

from factorio_circuit.analysis.latency import FACTORIO_LATENCY
from factorio_circuit.analysis.state_timing import StateTimingPlan
from factorio_circuit.ir.abstract_physical import Connector, ConstantCombinator, Endpoint
from factorio_circuit.ir.semantic import CircuitModule
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedValue, RealizedVector
from factorio_circuit.lowering.open_vector import VectorLowerer
from factorio_circuit.lowering.settling import SettlingVectorLowerer


class LevelAlignmentLowerer(SettlingVectorLowerer):
    """Level-only alignment with explicit reuse versus exact token transport."""

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

    def _reuse_scalar_at(self, value: RealizedValue, target_phase: int) -> RealizedValue:
        """View one already-valid Level scalar at a later phase without adding hardware."""

        window = self._scalar_window(value)
        if window is None or not window.contains(target_phase):
            raise ValueError("scalar Level is not proved valid at the requested phase")
        result = RealizedValue(
            signal=value.signal,
            net=value.net,
            phase=target_phase,
            clean_single_lane=value.clean_single_lane,
        )
        self._remember_scalar(result, window.from_phase(target_phase))
        return result

    def _reuse_vector_at(self, value: RealizedVector, target_phase: int) -> RealizedVector:
        """View one already-valid Level vector at a later phase without adding hardware."""

        window = self._vector_window(value)
        if window is None or not window.contains(target_phase):
            raise ValueError("vector Level is not proved valid at the requested phase")
        result = RealizedVector(value.net, target_phase)
        self._remember_vector(result, window.from_phase(target_phase))
        return result

    def exact_delay_to(self, value: RealizedValue, target_phase: int) -> RealizedValue:
        """Physically transport one exact scalar token, bypassing every alignment policy."""

        if value.phase > target_phase:
            raise ValueError("cannot delay backwards in time")
        if value.phase == target_phase:
            return value

        window = self._scalar_window(value)
        # Call the pre-settling implementation explicitly. Dynamic dispatch through ``delay_to`` is
        # exactly what this primitive must avoid: subclasses are free to interpret Level alignment
        # as late observation or planned shared transport.
        result = VectorLowerer.delay_to(self, value, target_phase)
        if window is not None:
            self._remember_scalar(
                result,
                self._window_after_exact_alignment(window, value.phase, target_phase),
            )
        return result

    def exact_delay_vector_to(
        self,
        value: RealizedVector,
        target_phase: int,
    ) -> RealizedVector:
        """Physically transport one exact vector token without Level-validity reuse."""

        if value.phase > target_phase:
            raise ValueError("cannot delay vector backwards in time")
        if value.phase == target_phase:
            return value

        window = self._vector_window(value)
        result = VectorLowerer.delay_vector_to(self, value, target_phase)
        if window is not None:
            self._remember_vector(
                result,
                self._window_after_exact_alignment(window, value.phase, target_phase),
            )
        return result

    def delay_to(self, value: RealizedValue, target_phase: int) -> RealizedValue:
        """Align a Level scalar by validity reuse when proved, otherwise exact transport."""

        if value.phase > target_phase:
            raise ValueError("cannot delay backwards in time")
        if value.phase == target_phase:
            return value
        window = self._scalar_window(value)
        if window is not None and window.contains(target_phase):
            return self._reuse_scalar_at(value, target_phase)
        return self.exact_delay_to(value, target_phase)

    def delay_vector_to(self, value: RealizedVector, target_phase: int) -> RealizedVector:
        """Align a Level vector by validity reuse when proved, otherwise exact transport."""

        if value.phase > target_phase:
            raise ValueError("cannot delay vector backwards in time")
        if value.phase == target_phase:
            return value
        window = self._vector_window(value)
        if window is not None and window.contains(target_phase):
            return self._reuse_vector_at(value, target_phase)
        return self.exact_delay_vector_to(value, target_phase)

    def _startup_ready(self, target_phase: int) -> RealizedValue:
        """Create the startup guard using explicit exact transport, never Level reuse."""

        input_phase = target_phase - FACTORIO_LATENCY.state_transition_latency("commit")
        if input_phase < 0:  # pragma: no cover - multicycle transitions have a preceding tick
            raise ValueError("multicycle state startup has no preceding physical tick")
        if self._startup_source is None:
            signal = self._new_signal("logical state startup")
            entity = ConstantCombinator(
                id=self._take_entity_id(),
                signals=((signal, 1),),
                description="logical state startup: constant one",
            )
            self.circuit.entities.append(entity)
            net = self._new_net(
                (signal,),
                Endpoint(entity.id, Connector.SINGLE),
                label="logical state startup",
            )
            self._startup_source = RealizedValue(signal, net, 0)
        return self.exact_delay_to(self._startup_source, input_phase)


__all__ = ["LevelAlignmentLowerer"]
