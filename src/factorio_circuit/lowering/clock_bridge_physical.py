"""Physical phase normalization for stateful cross-clock bridges.

``HoldInto`` currently elaborates to a compiler-owned ``FreezeRegister`` update plus a
``SampleOn`` read of that register on the target clock.  The two semantic pieces still form one
physical bridge: source data/valid and target valid must meet the bridge at one common latency from
their respective semantic occurrences.

The held register is a continuously visible Level net.  Sampling it at the bridge phase therefore
means observing that live net at the later phase, not inserting a delay line that would transport an
earlier memory value forward in time.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from typing import Any, cast

from factorio_circuit.analysis.state_timing import StateTimingPlan
from factorio_circuit.ir.abstract_physical import AbstractPhysicalCircuit
from factorio_circuit.ir.semantic import (
    CircuitModule,
    Clock,
    ClockId,
    SampleOn,
    VectorValue,
)
from factorio_circuit.ir.state import FreezeRegister, StateTransition, VectorRegisterRead
from factorio_circuit.lowering.derived_clock_physical import DerivedClockPhysicalLowerer
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedVector


class ClockBridgePhysicalLowerer(DerivedClockPhysicalLowerer):
    """Derived-clock lowerer with one shared execution phase per HoldInto-style bridge."""

    def __init__(self, module: CircuitModule, *, state_timing: StateTimingPlan) -> None:
        super().__init__(module, state_timing=state_timing)
        self._sampled_freeze_targets = self._collect_sampled_freeze_targets(module)
        self._hold_bridge_phases: dict[tuple[FreezeRegister, ClockId], int] = {}

    @staticmethod
    def _collect_sampled_freeze_targets(
        module: CircuitModule,
    ) -> dict[FreezeRegister, tuple[Clock, ...]]:
        """Find Event clocks that sample compiler-visible freeze state.

        ``Circuit.hold_into`` intentionally elaborates away before physical lowering.  Recovering
        this structural relation keeps the semantic IR small while still letting the backend lower
        the hidden register update and its target sample as one phase-aligned bridge.
        """

        targets: dict[FreezeRegister, list[Clock]] = {}
        seen: set[int] = set()

        def visit(value: object) -> None:
            if value is None or id(value) in seen:
                return
            seen.add(id(value))

            if isinstance(value, SampleOn) and isinstance(value.source, VectorRegisterRead):
                register = value.source.register
                if isinstance(register, FreezeRegister):
                    clocks = targets.setdefault(register, [])
                    if value.target.clock not in clocks:
                        clocks.append(value.target.clock)

            if isinstance(value, Mapping):
                for key, item in value.items():
                    visit(key)
                    visit(item)
                return
            if isinstance(value, (tuple, list, set, frozenset)):
                for item in value:
                    visit(item)
                return
            if is_dataclass(value) and not isinstance(value, type):
                for field in fields(cast(Any, value)):
                    visit(getattr(value, field.name))

        visit(module)
        return {register: tuple(clocks) for register, clocks in targets.items()}

    def _freeze_source(self, transition: StateTransition) -> RealizedVector:
        source = super()._freeze_source(transition)
        register = transition.register
        if not isinstance(register, FreezeRegister):
            return source
        targets = tuple(
            clock
            for clock in self._sampled_freeze_targets.get(register, ())
            if clock != transition.clock
        )
        if not targets:
            return source

        # All inputs to one stateful crossing use one latency measured from their own semantic
        # occurrence.  Derived target clocks may themselves need several combinator ticks before
        # their valid token exists, so include their native phase before choosing the bridge phase.
        target_clocks = tuple(self._clock_at(clock, 0) for clock in targets)
        bridge_phase = max(source.phase, *(clock.phase for clock in target_clocks))
        source = self.delay_vector_to(source, bridge_phase)
        for clock in targets:
            self._hold_bridge_phases[(register, clock.clock_id)] = bridge_phase
        return source

    def realize_vector(self, value: VectorValue) -> RealizedVector:
        if isinstance(value, SampleOn) and isinstance(value.source, VectorRegisterRead):
            register = value.source.register
            if isinstance(register, FreezeRegister):
                phase = self._hold_bridge_phases.get((register, value.target.clock.clock_id))
                if phase is not None:
                    cached = self.vector_memo.get(id(value))
                    if cached is not None:
                        return cached
                    self._event_flow(value)
                    state = self.state_outputs.get(register.name)
                    if state is None:  # pragma: no cover - state reservation precedes realization
                        raise ValueError(f"state register {register.name!r} was not reserved")
                    # This is deliberately a phase re-observation of a live Level net, not
                    # ``delay_vector_to``.  A delay line would preserve the old memory value from
                    # the earlier phase and defeat HoldInto's strict-prior boundary semantics.
                    result = RealizedVector(state.net, phase)
                    self.vector_memo[id(value)] = result
                    return result
        return super().realize_vector(value)


def lower_clock_bridge_physical(
    module: CircuitModule,
    *,
    state_timing: StateTimingPlan,
) -> AbstractPhysicalCircuit:
    """Lower Event/state/derived-clock modules with aligned stateful bridge phases."""

    return ClockBridgePhysicalLowerer(module, state_timing=state_timing).lower()


__all__ = ["ClockBridgePhysicalLowerer", "lower_clock_bridge_physical"]
