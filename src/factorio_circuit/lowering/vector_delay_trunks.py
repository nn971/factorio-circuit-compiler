"""Shared exact-delay trunks for production Level vector lowering.

Validity-aware Level alignment removes phase padding when a logical Level token is already certified
at a later physical phase. When exact transport is still required, multiple consumers of the same
vector should nevertheless share one delay prefix, just as scalar exact transport reuses its private
prefix cache.
"""

from __future__ import annotations

from factorio_circuit.analysis.latency import FACTORIO_LATENCY
from factorio_circuit.analysis.state_timing import StateTimingPlan
from factorio_circuit.ir.abstract_physical import ArithmeticCombinator, Connector, Endpoint, Operand
from factorio_circuit.ir.semantic import CircuitModule
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedVector
from factorio_circuit.lowering.level_alignment import LevelAlignmentLowerer


class SharedVectorDelayLowerer(LevelAlignmentLowerer):
    """Level lowerer with memoized prefixes for exact vector transport."""

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
        self.vector_delay_cache: dict[tuple[int, int], RealizedVector] = {}

    def exact_delay_vector_to(
        self,
        value: RealizedVector,
        target_phase: int,
    ) -> RealizedVector:
        """Transport one exact vector token while sharing every already-built prefix."""

        if value.phase > target_phase:
            raise ValueError("cannot delay vector backwards in time")
        if value.phase == target_phase:
            return value

        window = self._vector_window(value)
        result = self._exact_vector_delay_to(value, target_phase)
        if window is not None:
            self._remember_vector(
                result,
                self._window_after_exact_alignment(window, value.phase, target_phase),
            )
        return result

    def _exact_vector_delay_to(
        self,
        value: RealizedVector,
        target_phase: int,
    ) -> RealizedVector:
        """Emit exact vector transport, sharing only prefixes of the same physical token."""

        current = value
        latency = FACTORIO_LATENCY.operation_latency("vector_binary", "delay")
        while current.phase < target_phase:
            next_phase = current.phase + latency
            key = (current.net, next_phase)
            cached = self.vector_delay_cache.get(key)
            if cached is not None:
                current = cached
                continue

            source = self.net_builders[current.net]
            entity = ArithmeticCombinator(
                id=self._take_entity_id(),
                operation="+",
                left=Operand(each=True, nets=(current.net,)),
                right=Operand(constant=0),
                output_each=True,
                description="vector phase alignment delay",
            )
            self.circuit.entities.append(entity)
            self._attach(current.net, Endpoint(entity.id, Connector.INPUT))
            output_net = self._new_net(
                source.signals,
                Endpoint(entity.id, Connector.OUTPUT),
                label="vector phase alignment delay",
                fixed_signals=source.fixed_signals,
                carries_dynamic_vector=source.carries_dynamic_vector,
            )
            current = RealizedVector(output_net, next_phase)
            self.vector_delay_cache[key] = current
        return current


__all__ = ["SharedVectorDelayLowerer"]
